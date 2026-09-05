"""The cheap-model step loop — perceive → propose → act (ADR-0003 D6).

This is the **brain** of the skill execute leg: it lets a small text model (e.g.
``qwen3:4b``) drive a real Chrome page **one action per step**. Each step

  1. **perceives** the page via ``page.snapshot()`` (issue 01's
     ``[{ref, role, name, value}]`` projection),
  2. builds the step **system prompt** from the SKILL.md 9 elements + that
     snapshot (:func:`backend.skills.prompt.build_system_prompt`),
  3. asks the model for **exactly one** action — reusing the agent dock's
     tool-calling harness (OpenAI ``tool_calls`` for normal models, the Qwen XML
     ``<tool_use>`` variant for ``qwable``-style models, both normalized via the
     reused parsers from ``backend.api.v1.chat``),
  4. **validates** it against issue 02's verb schema
     (``backend.skills.actions.validate_action``),
  5. **executes** it through issue 02's executor
     (``backend.skills.actions.execute_action``), and
  6. feeds the ``action -> result`` back into the transcript,

looping until the model emits ``done{}`` (validated against
``terminal_conditions`` / ``false_terminal_states`` — a ``done`` that trips a
false-terminal phrase is **rejected** and the loop continues) or a ``max_steps``
cap is hit.

**Scope boundary (issue 03 only).** The loop is *pure of the spine*: it does
**not** emit events, open a DB session, classify risk, gate writes, acquire a
browser-pool slot, or build a ``ChannelResult``. Every action auto-runs (the
risk/confirm gate is issue 04). It returns plain Python data (:class:`LoopResult`)
that issues 04/05/06 consume. Provider **resolution** is the caller's job — the
loop receives an already-bound ``model_call`` and the resolved ``model`` name.
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol, runtime_checkable

from backend.skills import actions
from backend.skills.prompt import (
    SKILL_TOOLS,
    SKILL_TOOLS_TEXT,
    build_system_prompt,
)
from backend.skills.risk import (
    AWAITING_CONFIRM,
    classify_action,
    should_run,
)
from backend.skills.toolcall import (
    _is_xml_tool_model,
    _parse_tool_use,
    _safe_json,
)

# Max steps before the loop gives up without a `done` (ADR-0003 D6: "~20").
MAX_STEPS = 20

# How many prior (action -> result) turns to keep in the model transcript. The
# cheap model has ~32k ctx and each step re-sends the full snapshot, so the
# running history is bounded to the most recent turns to avoid blow-up.
_TRANSCRIPT_WINDOW = 12


@runtime_checkable
class SkillPage(Protocol):
    """Minimal page boundary the loop perceives through (issue 01 satisfies it).

    The loop only needs to *perceive*; it acts exclusively through issue 02's
    executor (``actions.execute_action(page, snapshot, action)``), so this
    Protocol stays thin. ``url`` is optional context for step records.
    """

    async def snapshot(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]: ...


@dataclass
class StepRecord:
    """One ordered step of the loop. Plain/dict-able for issues 05 & 06.

    ``terminal_check`` is set only on a ``done`` step
    (``accepted`` | ``rejected``).
    """

    index: int
    verb: str | None
    args: dict[str, Any]
    target: Any = None
    snapshot_digest: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    terminal_check: str | None = None
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LoopResult:
    """Raw material issues 05 (ChannelResult) & 06 (journey_trace_v1) consume.

    ``outcome`` ∈ ``{done_success, done_failed, capped, error, awaiting_confirm}``.
    ``steps`` is ordered; ``extracts`` accumulates ``extract`` payloads in order.
    ``awaiting_confirm`` is set when the risk gate (issue 04) blocked a write in
    headless mode; ``proposed_action`` is then the action the operator must
    confirm (issue 05 lifts both onto ``ChannelResult.metadata``).
    """

    steps: list[StepRecord] = field(default_factory=list)
    extracts: list[dict[str, Any]] = field(default_factory=list)
    outcome: str = "error"
    summary: dict[str, Any] = field(default_factory=dict)
    awaiting_confirm: bool = False
    proposed_action: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": [s.to_dict() for s in self.steps],
            "extracts": self.extracts,
            "outcome": self.outcome,
            "summary": self.summary,
            "awaiting_confirm": self.awaiting_confirm,
            "proposed_action": self.proposed_action,
        }


def _digest_snapshot(snapshot: list[dict[str, Any]]) -> str:
    """Tiny, bounded fingerprint of a snapshot for the step record."""
    if not snapshot:
        return "0 elements"
    refs = [str(el.get("ref")) for el in snapshot[:8]]
    more = "..." if len(snapshot) > 8 else ""
    return f"{len(snapshot)} elements [{','.join(refs)}{more}]"


def _normalize_reply(reply: Any, *, xml: bool) -> tuple[list[tuple[str, dict[str, Any]]], str]:
    """Normalize a raw model reply into ``[(verb, args), ...]`` + raw content.

    Handles **both** tool-call shapes with the *same* parsers the agent dock
    uses (reuse, not fork — ``backend.api.v1.chat``):

      * **XML path** (``xml=True``): parse ``<tool_use>`` from message content
        via ``_parse_tool_use`` (which strips nothing itself — the regex already
        ignores surrounding text; ``<think>`` blocks don't match the tool regex).
      * **OpenAI path**: read ``reply.choices[0].message.tool_calls`` exactly as
        ``chat.chat()`` does, decoding each call's JSON args with ``_safe_json``.

    Returns the *ordered* list of parsed calls (the loop takes the first) and the
    assistant ``content`` (for the transcript).
    """
    message = reply.choices[0].message
    content = getattr(message, "content", "") or ""

    if xml:
        return _parse_tool_use(content), content

    calls: list[tuple[str, dict[str, Any]]] = []
    for tc in (getattr(message, "tool_calls", None) or []):
        name = tc.function.name
        calls.append((name, _safe_json(tc.function.arguments)))
    return calls, content


def _check_done(
    action: dict[str, Any],
    snapshot: list[dict[str, Any]],
    elements: dict | None,
) -> str:
    """Validate a claimed ``done`` against the 9 elements (ADR-0003 D6).

    Conservative, NL-tolerant heuristic (the executor is a cheap model and the
    conditions are free text): a ``done`` is **rejected** when its ``note`` or
    the current page text trips any ``false_terminal_states`` phrase. Otherwise
    accepted. Returns ``"accepted"`` | ``"rejected"``. Never trusts ``done``
    blindly, but stays permissive enough not to deadlock a correct completion.
    """
    fts = (elements or {}).get("false_terminal_states") or []
    if not fts:
        return "accepted"

    # Haystack: the model's own note + the visible names/values in the snapshot.
    note = str(action.get("note") or "")
    snap_text = " ".join(
        f"{el.get('name', '')} {el.get('value', '')}" for el in (snapshot or [])
    )
    haystack = (note + " " + snap_text).lower()

    for phrase in fts:
        p = str(phrase).strip().lower()
        if p and p in haystack:
            return "rejected"
    return "accepted"


def _outcome_for_done(status: Any) -> str:
    """Map a ``done`` status onto the LoopResult outcome vocabulary."""
    return "done_success" if str(status).lower() == "success" else "done_failed"


async def run_skill_loop(
    *,
    page: SkillPage,
    model_call: Any,
    model: str = "qwen3:4b",
    skill_md: str | None = None,
    elements: dict | None = None,
    task: str | None = None,
    max_steps: int = MAX_STEPS,
    skill: Any = None,
    auto_confirm: bool = False,
    run_id: str | None = None,
    emit: Any = None,
) -> LoopResult:
    """Run the perceive → propose → act loop until ``done`` or ``max_steps``.

    Parameters
    ----------
    page:
        A :class:`SkillPage` (issue 01) — the loop calls ``await page.snapshot()``
        to perceive; all *acting* goes through issue 02's executor.
    model_call:
        An ``async (messages, *, tools, model, xml) -> reply`` callable already
        bound to the resolved provider (issue 05 binds it; the test scripts it).
        ``reply`` is an OpenAI-chat-shaped object
        (``reply.choices[0].message`` with ``.tool_calls`` / ``.content``).
    model:
        Resolved model name — only used to pick the OpenAI vs XML tool path via
        ``_is_xml_tool_model`` (reused from the agent dock).
    skill_md / elements:
        The SKILL.md 9 elements — structured ``elements`` preferred, raw
        ``skill_md`` as fallback (see :func:`build_system_prompt`).
    task:
        Optional task description injected into the prompt.
    max_steps:
        Cap before terminating with ``outcome="capped"`` (default :data:`MAX_STEPS`).
    skill:
        The ``Skill`` row / ``elements`` dict carrying ``red_lines``, passed to
        :func:`backend.skills.risk.classify_action` (issue 04). ``None`` (the
        default) means no red lines — only the generic high-risk pattern applies.
    auto_confirm:
        Risk-gate bypass (``DataSource.channel_config["auto_confirm"]``, default
        ``False``). When ``True`` a confirm-required action runs unattended; when
        ``False`` a blocked write aborts the headless loop at
        :data:`~backend.skills.risk.AWAITING_CONFIRM`.
    run_id:
        Optional run id; when set, the gate emits a per-step ``awaiting_confirm``
        :class:`~backend.models.task.TaskRunEvent` via ``events.emit`` on a
        block. Best-effort — ``None`` (e.g. unit tests) just skips the event.

    Returns
    -------
    LoopResult
        Ordered ``steps`` + accumulated ``extracts`` + ``outcome`` + ``summary``.
        On a headless gate block: ``outcome="awaiting_confirm"``,
        ``awaiting_confirm=True``, ``proposed_action=<blocked action>``.
    """
    result = LoopResult()
    xml = _is_xml_tool_model(model)
    transcript: list[dict[str, Any]] = []  # running (assistant/user) turns

    index = 0
    while index < max_steps:
        # 1. perceive
        snapshot = await page.snapshot()
        digest = _digest_snapshot(snapshot)

        # 2. build the step system prompt (9 elements + snapshot)
        system = build_system_prompt(
            skill_md=skill_md,
            elements=elements,
            snapshot=snapshot,
            task=task,
            step_index=index,
            max_steps=max_steps,
        )
        if xml:
            system += SKILL_TOOLS_TEXT
        messages = [{"role": "system", "content": system}, *transcript]

        # 3. ask the model for one action
        started = time.monotonic()
        try:
            reply = await model_call(
                messages, tools=None if xml else SKILL_TOOLS, model=model, xml=xml
            )
        except Exception as exc:  # provider error → record + terminate cleanly
            result.steps.append(
                StepRecord(
                    index=index,
                    verb=None,
                    args={},
                    snapshot_digest=digest,
                    error=f"model call failed: {exc}",
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
            )
            result.outcome = "error"
            result.summary = _summarize(result, index + 1)
            return result

        calls, content = _normalize_reply(reply, xml=xml)
        elapsed = int((time.monotonic() - started) * 1000)

        # No tool call → nudge the model and continue (don't crash a confused model).
        if not calls:
            result.steps.append(
                StepRecord(
                    index=index,
                    verb=None,
                    args={},
                    snapshot_digest=digest,
                    error="no tool call emitted",
                    elapsed_ms=elapsed,
                )
            )
            _push(transcript, content, "No tool call detected. Emit EXACTLY ONE tool call.")
            index += 1
            continue

        # One action/step is the contract: take the first, note any truncation.
        verb, args = calls[0]
        truncated = len(calls) > 1
        action = {"verb": verb, **(args or {})}

        # 4. validate against issue 02's schema
        verr = actions.validate_action(action)
        if verr is not None:
            step = StepRecord(
                index=index,
                verb=verb,
                args=dict(args or {}),
                snapshot_digest=digest,
                error=verr,
                elapsed_ms=elapsed,
            )
            if truncated:
                step.error += " (extra tool calls ignored: one action/step)"
            result.steps.append(step)
            _push(transcript, content, f'<tool_result name="{verb}">error: {verr}</tool_result>')
            index += 1
            continue

        # 5. done → validate the claimed completion; do NOT execute as a page op.
        if verb == "done":
            check = _check_done(action, snapshot, elements)
            result.steps.append(
                StepRecord(
                    index=index,
                    verb="done",
                    args=dict(args or {}),
                    target=args.get("status"),
                    snapshot_digest=digest,
                    result={"status": args.get("status"), "note": args.get("note")},
                    terminal_check=check,
                    elapsed_ms=elapsed,
                )
            )
            if check == "accepted":
                result.outcome = _outcome_for_done(args.get("status"))
                result.summary = _summarize(result, index + 1, final_status=args.get("status"))
                return result
            # rejected → feed the rejection back and keep going (don't stop).
            _push(
                transcript,
                content,
                '<tool_result name="done">rejected: a false_terminal_state applies; '
                "the task is not actually complete. Keep going.</tool_result>",
            )
            index += 1
            continue

        # 6. risk gate (issue 04) — classify BEFORE execution; block writes in
        #    headless mode unless auto_confirm bypasses. Reads/nav/scroll/extract
        #    auto-run; red_lines / submit|pay|post|delete need confirm.
        target_element = (
            actions.resolve_ref(snapshot, action["ref"])
            if action.get("ref") is not None
            else None
        )
        decision = classify_action(action, target_element, skill)
        if not should_run(decision, auto_confirm):
            # Headless v1: abort cleanly. Interactive synchronous resume (the dock
            # round-trip) is issues 05/06; here the testable behavior is the abort
            # + the awaiting_confirm signal surfaced for the channel to lift onto
            # ChannelResult.metadata (and later runner Phase 4 → run.status).
            if run_id and emit is not None:
                await emit(
                    run_id,
                    AWAITING_CONFIRM,
                    f"awaiting confirm: {verb} ({decision.reason})",
                    level="warning",
                    detail={
                        "action": action,
                        "decision": decision.to_dict(),
                        "matched_red_line": decision.matched_red_line,
                    },
                    elapsed_ms=elapsed,
                )
            result.steps.append(
                StepRecord(
                    index=index,
                    verb=verb,
                    args=dict(args or {}),
                    target=_target_of(action),
                    snapshot_digest=digest,
                    result={"gate": "blocked", "decision": decision.to_dict()},
                    error=f"awaiting_confirm: {decision.reason}",
                    elapsed_ms=elapsed,
                )
            )
            result.outcome = AWAITING_CONFIRM
            result.awaiting_confirm = True
            result.proposed_action = action
            result.summary = _summarize(result, index + 1)
            return result

        # 7. execute via issue 02's executor (auto-run, or auto_confirm bypass)
        exec_result = await actions.execute_action(page, snapshot, action)

        # 8. extract → accumulate (issue 05 surfaces these as ChannelResult.items)
        if verb == "extract" and exec_result.ok and exec_result.record is not None:
            result.extracts.append(exec_result.record)

        # 9. ordered step record + feed action -> result back into the transcript
        step = StepRecord(
            index=index,
            verb=verb,
            args=dict(args or {}),
            target=_target_of(action),
            snapshot_digest=digest,
            result={"ok": exec_result.ok, "detail": exec_result.detail},
            error=exec_result.error,
            elapsed_ms=elapsed,
        )
        if truncated:
            step.result["truncated_extra_calls"] = True
        result.steps.append(step)

        feedback = (
            f"ok: {exec_result.detail}" if exec_result.ok else f"error: {exec_result.error}"
        )
        _push(transcript, content, f'<tool_result name="{verb}">{feedback}</tool_result>')
        index += 1

    # 10. cap hit without done
    result.outcome = "capped"
    result.summary = _summarize(result, index)
    return result


# ── transcript / summary helpers ───────────────────────────────────────────────
def _push(transcript: list[dict[str, Any]], assistant: str, user: str) -> None:
    """Append an assistant turn + its tool-result user turn; keep it bounded."""
    transcript.append({"role": "assistant", "content": assistant or ""})
    transcript.append({"role": "user", "content": user})
    # Bound the running history (window counts assistant+user pairs).
    if len(transcript) > _TRANSCRIPT_WINDOW * 2:
        del transcript[: len(transcript) - _TRANSCRIPT_WINDOW * 2]


def _target_of(action: dict[str, Any]) -> Any:
    """Best-effort 'what this action addressed' for the step record."""
    for key in ("ref", "url", "dir"):
        if key in action:
            return action[key]
    return None


def _summarize(
    result: LoopResult, step_count: int, *, final_status: Any = None
) -> dict[str, Any]:
    """Small forward-compatible summary for issues 05 & 06."""
    return {
        "step_count": step_count,
        "extract_count": len(result.extracts),
        "outcome": result.outcome,
        "final_status": final_status,
    }
