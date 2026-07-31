"""Skill channel — execute a distilled SKILL.md against a real page.

The closed-loop **execute** leg of the skill subsystem (ADR-0003): read a
distilled SKILL.md card, bind a real browser from the shared pool (same CDP
substrate the opencli channel uses), and let a *cheap* text model drive the page
step by step until it emits ``done`` or hits the step cap.

This file is the **spine seam** (issue 05): it stays inside the existing
``task → run → pipeline → events → record`` flow without changing
``AbstractChannel.collect``'s signature. The perceive→gate→act loop itself lives
in :mod:`backend.skills.loop` (issue 03); the risk-tiered confirm gate lives in
:mod:`backend.skills.risk` (issue 04); this channel only *drives* them:

  * read ``run_id`` / ``chrome_endpoint`` out of ``parameters`` (the pipeline
    injects them — :func:`backend.pipeline.pipeline.run_pipeline` mirrors its
    ``opencli`` special-case),
  * acquire a CDP endpoint from :mod:`backend.browser_pool` and attach a
    Playwright page (issue 01's :class:`backend.skills.page.SkillPage`),
  * resolve the cheap-executor ``provider`` and bind a ``model_call`` to it
    (reusing the agent dock's OpenAI tool-calling shape),
  * run the loop (:func:`backend.skills.loop.run_skill_loop`), emit one
    ``TaskRunEvent`` per step via ``events.emit(run_id, ...)``,
  * return ``extract`` records as :class:`ChannelResult` items and propagate
    ``awaiting_confirm`` in ``ChannelResult.metadata`` (the loop sets it when the
    gate blocks a write in headless v1).

Issue 06 adds the **feedback** leg on top of this seam: after the loop, the
channel assembles a ``journey_trace_v1`` from the loop's own step records +
outcome (:func:`backend.skills.trace.assemble_trace`), computes a
:func:`~backend.skills.trace.self_eval` against the skill's
``terminal_conditions`` / ``milestones``, appends that self-eval to
``skills.evidence`` (when a persisted :class:`~backend.models.skill.Skill` row is
resolvable), and surfaces the trace on ``ChannelResult.metadata['trace']`` (the
``correct`` leg / re-distill lives in :mod:`backend.skills.correction`).

``skill_id`` / ``(domain, capability)`` → DB resolution for *loading* the
SKILL.md is wired in :func:`_resolve_skill` (a short-lived ``AsyncSessionLocal``,
same pattern as the self-eval write — ``collect()`` holds no injected session).
Still deferred (v2): cross-process pause/resume of an ``awaiting_confirm`` run.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from backend.channels.base import AbstractChannel, Capabilities, ChannelResult
from backend.channels.registry import register_channel
from backend.llm.base import LlmAdapterError
from backend.llm.factory import build_openai_compat_adapter
from backend.pipeline import events

# risk / perception import only stdlib — safe at registry-load time. The loop is
# imported lazily inside collect() because backend.skills.loop imports
# backend.api.v1.chat, and the channel registry is itself loaded *from* chat's
# import chain (registry → skill_channel → skills.loop → api.v1.chat = cycle).
from backend.skills import perception
from backend.skills.risk import AWAITING_CONFIRM, PROPOSED_ACTION
from backend.skills.trace import assemble_trace, outcome_from_loop, self_eval

if TYPE_CHECKING:  # typing only — keep the LoopResult import out of the cycle
    from backend.skills.loop import LoopResult

logger = logging.getLogger(__name__)

# Step names emitted into TaskRunEvent.step (free-text String(50)). The run-events
# UI / acceptance tests key on these exact strings (PRD §6; ``self_eval`` is
# issue 06). ``awaiting_confirm`` is emitted by the loop itself when the gate
# blocks a write — see backend.skills.loop / backend.skills.risk.
STEP_PERCEIVE = "skill_perceive"
STEP_STEP = "skill_step"
STEP_EXTRACT = "skill_extract"
STEP_DONE = "skill_done"


async def _load_skill_fields(
    skill_id: str | None, domain: str | None, capability: str | None
) -> dict[str, Any] | None:
    """Load a persisted Skill's fields via a short-lived session.

    ``collect()`` holds no injected session, so — exactly like ``_append_self_eval``
    and ``events.emit`` — we open our own ``AsyncSessionLocal()``. Resolves by
    ``skill_id`` first, then the unique ``(domain, capability)``. Reads the needed
    columns *inside* the session and returns a plain dict so the caller never
    touches a detached ORM instance; ``None`` when no row matches.
    """
    from sqlalchemy import select

    from backend.database import AsyncSessionLocal
    from backend.models.skill import Skill

    async with AsyncSessionLocal() as session:
        skill: Skill | None = None
        if skill_id:
            skill = await session.get(Skill, skill_id)
        if skill is None and domain and capability:
            res = await session.execute(
                select(Skill).where(Skill.domain == domain, Skill.capability == capability)
            )
            skill = res.scalars().first()
        if skill is None:
            return None
        return {
            "id": skill.id,
            "skill_md": skill.skill_md,
            "elements": skill.elements,
            "domain": skill.domain,
            "capability": skill.capability,
            "version": skill.version,
            "enabled": skill.enabled,
        }


async def _resolve_skill(
    config: dict[str, Any],
) -> tuple[str | None, dict | None, dict[str, Any], str | None]:
    """Resolve ``(skill_md, elements, identity, error)`` for a run.

    Inline ``config['skill_md']`` wins (fast path, no DB). Otherwise load the
    persisted skill by ``skill_id`` or ``(domain, capability)`` — the
    "SkillService" leg (ADR-0003): the channel reads the stored SKILL.md +
    structured elements so a distilled skill executes straight from the DB
    without re-supplying its body. ``identity`` carries the resolved
    ``skill_id`` / ``domain`` / ``capability`` / ``version`` so the trace +
    self-eval write back to the right row.
    """
    skill_md = config.get("skill_md")
    if skill_md:
        return skill_md, _resolve_elements(config), {}, None

    skill_id = config.get("skill_id")
    domain = config.get("domain")
    capability = config.get("capability")
    if not skill_id and not (domain and capability):
        return None, None, {}, (
            "skill channel requires config['skill_md'], or 'skill_id', or "
            "('domain' + 'capability')."
        )

    try:
        fields = await _load_skill_fields(skill_id, domain, capability)
    except Exception as exc:  # DB failure — surface as a clean fail, don't crash collect
        logger.error("skill resolve | DB load failed: %s", exc)
        return None, None, {}, f"skill resolve failed: {exc}"

    if fields is None:
        ident = skill_id or f"{domain}/{capability}"
        return None, None, {}, f"skill not found: {ident}"
    if fields["enabled"] is False:
        return None, None, {}, f"skill {fields['id']} is disabled — enable it before executing."
    md = fields["skill_md"] or ""
    if not md:
        return None, None, {}, f"skill {fields['id']} has empty skill_md."

    elements = (
        fields["elements"] if isinstance(fields["elements"], dict) and fields["elements"] else None
    )
    identity = {
        "skill_id": fields["id"],
        "domain": fields["domain"],
        "capability": fields["capability"],
        "version": fields["version"],
    }
    return md, elements, identity, None


def _resolve_elements(config: dict[str, Any]) -> dict | None:
    """Pull the structured 9-element dict (Skill.elements shape) from config.

    The loop/prompt builder prefer structured ``elements`` (each loop-control
    section addressable) and fall back to the raw ``skill_md`` when absent. For
    v1 the elements may be supplied inline alongside ``skill_md``; ``None`` means
    "use ``skill_md`` verbatim".
    """
    elements = config.get("elements")
    return elements if isinstance(elements, dict) and elements else None


class _PerceivingPage:
    """Adapter giving the loop a single ``page`` that can both *perceive* and *act*.

    The loop (issue 03) perceives through ``await page.snapshot()`` and acts
    through issue 02's executor (``page.goto/click/type/select/scroll`` etc.).
    Issue 01's :class:`~backend.skills.page.SkillPage` provides the raw ops but
    no ``snapshot()`` (perception is a separate module). This thin wrapper adds
    ``snapshot()`` by delegating to :func:`backend.skills.perception.snapshot`
    on the underlying Playwright page, and forwards every other attribute to the
    wrapped ``SkillPage`` so the executor's page ops keep working unchanged.
    """

    def __init__(self, skill_page: Any) -> None:
        self._sp = skill_page

    async def snapshot(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        return await perception.snapshot(self._sp.page, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        # Forward goto/click/type/select/scroll/inner_text/extract to the SkillPage.
        return getattr(self._sp, name)


async def _build_model_call(provider: dict[str, Any]) -> Any:
    """Bind an ``async (messages, *, tools, model, xml) -> reply`` model caller.

    Reuses the agent dock's OpenAI-compatible client shape
    (:class:`openai.AsyncOpenAI` + ``chat.completions.create``) so the cheap
    executor model (e.g. ``qwen3:4b`` behind an OpenAI-compatible / Ollama
    gateway) is driven exactly like ``backend.api.v1.chat`` drives the console
    model. ``reply`` is the raw OpenAI chat object the loop already knows how to
    normalize (both ``tool_calls`` and the Qwen XML ``<tool_use>`` path).

    model-provider runtime PR-E: client construction is consolidated through
    :class:`~backend.llm.openai_compat.OpenAICompatAdapter` (via
    :func:`~backend.llm.factory.build_openai_compat_adapter`) — the same
    guarded ``AsyncOpenAI`` construction ``chat.py`` and the ``openai``
    processor now also go through, instead of this file hand-rolling its own
    copy of the SSRF-guard + DNS-rebind-pinning wiring. Behavior preserved
    exactly: ``provider`` is a plain ``dict`` (``channel_config.provider``)
    with no ``provider_type`` key, so the adapter's ``allow_private`` stays
    ``False`` — the full, unmodified SSRF guard — exactly as this file's own
    ``avalidate_public_url_and_ip(base_url)`` call (no ``allow_private=True``)
    already enforced before PR-E.

    Key-exfil guard: ``provider`` is DB/config-supplied (``channel_config.
    provider``), so its ``base_url`` is validated before the API key is ever
    attached to a client pointed at it — an unvalidated base_url would let a
    malicious/misconfigured source ship the LLM key to an internal host or an
    attacker-controlled public endpoint. No base_url configured (provider's
    own default) is left unvalidated.

    DNS-rebinding closure (AUDIT B3 follow-up): ``AsyncOpenAI`` accepts an
    ``http_client`` (any ``httpx.AsyncClient``), so when ``base_url`` is
    validated we also pin the connection to the resolved IP(s) via a
    :class:`~backend.security.url_guard.PinnedAsyncHTTPTransport` — the same
    mechanism ``guarded_async_client`` uses for plain-httpx call sites. No
    base_url configured leaves ``http_client`` unset (AsyncOpenAI's own
    default client), unchanged from before.
    """
    api_key = provider.get("api_key") or ""
    base_url = provider.get("base_url") or None
    adapter = build_openai_compat_adapter(base_url=base_url, api_key=api_key)
    try:
        client = await adapter.get_client()
    except LlmAdapterError as exc:
        raise ValueError(f"skill channel: {exc}") from exc

    async def model_call(
        messages: list[dict[str, Any]], *, tools: Any, model: str, xml: bool
    ) -> Any:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        # OpenAI tool path passes the skill verb schema; the XML path describes
        # the verbs in the prompt and sends no tools (mirrors chat._chat_xml).
        if not xml and tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return await client.chat.completions.create(**kwargs)

    return model_call


def _emit_loop_events(result: LoopResult) -> list[dict[str, Any]]:
    """Build the per-step ``TaskRunEvent`` payloads for a finished loop.

    The loop is *pure of the spine* (it only self-emits ``awaiting_confirm`` on a
    gate block); spine event emission is this channel's job. We walk the ordered
    ``result.steps`` and build one event payload each — ``skill_extract`` for
    ``extract`` verbs, ``skill_step`` for everything else — bracketed by a
    leading ``skill_perceive`` and a trailing ``skill_done`` carrying the
    outcome.

    AUDIT C24: this used to return a list of already-built ``events.emit(...)``
    coroutines, awaited one at a time by the caller (one session + INSERT +
    commit/fsync per step). It now returns plain dicts so the caller can hand
    the whole trace to ``events.emit_many`` — one session, one bulk insert, one
    commit for the entire run's step trace.
    """
    payloads: list[dict[str, Any]] = [
        {
            "step": STEP_PERCEIVE,
            "message": f"开始执行技能 | 步数={len(result.steps)}",
            "detail": {"step_count": len(result.steps)},
        }
    ]
    for step in result.steps:
        verb = step.verb or "?"
        is_extract = verb == "extract"
        payloads.append(
            {
                "step": STEP_EXTRACT if is_extract else STEP_STEP,
                "message": f"步骤 {step.index} | {verb}"
                + (f" | 错误: {step.error}" if step.error else ""),
                "level": "warning" if step.error else "info",
                "detail": {
                    "index": step.index,
                    "verb": step.verb,
                    "target": step.target,
                    "error": step.error,
                    "result": step.result,
                },
                "elapsed_ms": step.elapsed_ms,
            }
        )
    payloads.append(
        {
            "step": STEP_DONE,
            "message": f"技能执行结束 | 结果={result.outcome} 提取={len(result.extracts)}",
            "level": "warning" if result.outcome in ("error", "done_failed") else "info",
            "detail": {
                "outcome": result.outcome,
                "extract_count": len(result.extracts),
                "awaiting_confirm": result.awaiting_confirm,
                "summary": result.summary,
            },
        }
    )
    return payloads


def _extracts_to_items(result: LoopResult) -> list[dict[str, Any]]:
    """Turn the loop's ``extract`` payloads into dict-shaped collected records.

    Each ``extract{data}`` payload is already a free-form dict the model read off
    the page. We pass it straight through (a shallow copy) so the normal
    ``normalizer.normalize_items`` → ``storer.store_records`` path stores + dedups
    it like any other channel's record — no skill-specific store branch. The
    normalizer keys off ``title``/``url``/``content`` aliases and falls back to a
    full-payload hash when none are present, so an arbitrary extract still stores
    and dedups deterministically.
    """
    items: list[dict[str, Any]] = []
    for payload in result.extracts:
        items.append(dict(payload) if isinstance(payload, dict) else {"value": payload})
    return items


def _step_records(result: LoopResult) -> list[dict[str, Any]]:
    """Build the trace ``steps[]`` from the loop's *own* ordered step records.

    One dict per loop step (issue 06 acceptance #1). We read ``result.steps``
    directly — the loop accumulated them in memory — rather than re-querying the
    best-effort ``TaskRunEvent`` rows ``events.emit`` writes (``collect()`` has no
    DB session, and emit is fire-and-forget). Each :class:`StepRecord` already
    carries verb / target / snapshot digest / result / timing.
    """
    return [s.to_dict() for s in result.steps]


def _step_haystack(result: LoopResult) -> str:
    """Lowercased blob of the run's own step results/errors + final summary —
    the NL-tolerant text both ``_milestones_hit`` and
    ``_terminal_conditions_hit`` match declared phrases against."""
    parts: list[str] = [str(result.summary)]
    for step in result.steps:
        parts.append(str(step.result))
        if step.error:
            parts.append(str(step.error))
    return " ".join(parts).lower()


def _milestones_hit(result: LoopResult, elements: dict | None) -> list[Any]:
    """Best-effort: which declared milestones the run plausibly reached.

    The cheap loop does not emit structured milestone signals, so we use a
    conservative NL-tolerant heuristic mirroring ``loop._check_done``: a declared
    milestone counts as hit when its phrase appears in any step's note/detail or
    the final summary. Empty when none declared. This is a *signal*, not a gate.
    """
    declared = (elements or {}).get("milestones") or []
    if not declared:
        return []
    haystack = _step_haystack(result)
    return [m for m in declared if str(m).strip() and str(m).strip().lower() in haystack]


def _terminal_conditions_hit(result: LoopResult, elements: dict | None) -> list[Any]:
    """Best-effort: which declared ``terminal_conditions`` the run's own step
    text actually mentions (2026-07-01 addendum).

    Closes a real gap: ``terminal_conditions`` is one of the 9 distilled
    elements but nothing ever checked it against anything —
    ``loop._check_done`` only validates the *negative* ``false_terminal_states``
    trap list, never the positive terminal-condition text, so
    :func:`backend.skills.trace.self_eval`'s ``terminal_met`` used to just alias
    ``succeeded`` regardless of what ``terminal_conditions`` declared (dead
    branch — see its 2026-07-01 fix). Same NL-tolerant heuristic as
    ``_milestones_hit``; empty when none declared, in which case ``self_eval``
    falls back to trusting ``succeeded`` alone (nothing to check against).
    """
    declared = (elements or {}).get("terminal_conditions") or []
    if not declared:
        return []
    haystack = _step_haystack(result)
    return [t for t in declared if str(t).strip() and str(t).strip().lower() in haystack]


def _terminal_check(result: LoopResult) -> Any:
    """The loop's terminal verdict for the trace ``outcome`` block.

    On a ``done`` step the loop records ``terminal_check`` (``accepted`` /
    ``rejected``); we surface the last such verdict. ``True`` for a clean
    ``done_success`` with no recorded check, else ``None``.
    """
    for step in reversed(result.steps):
        if step.verb == "done" and step.terminal_check is not None:
            return step.terminal_check
    return True if result.outcome == "done_success" else None


async def _append_self_eval(
    config: dict[str, Any], ev: dict[str, Any]
) -> bool:
    """Append a self-eval entry to the resolvable Skill's ``evidence`` (D7).

    Opens a short-lived ``AsyncSessionLocal()`` (same pattern as ``events.emit`` —
    ``collect()`` holds no session), loads the :class:`Skill` by ``skill_id`` or
    ``(domain, capability)``, appends ``ev`` to its ``evidence`` list (reassigning
    the attribute so SQLAlchemy detects the JSON mutation), and commits. Returns
    ``True`` if it wrote, ``False`` for the inline-skill case (no persisted row) —
    best-effort, never raises (mirrors ``events.emit``).
    """
    skill_id = config.get("skill_id")
    domain = config.get("domain")
    capability = config.get("capability")
    if not skill_id and not (domain and capability):
        return False
    try:
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.skill import Skill

        async with AsyncSessionLocal() as session:
            # with_for_update: this is a read-append-commit on a JSON column —
            # a plain read would race two concurrent runs of the same skill
            # (2026-07-01: last commit silently wins, the other append is lost,
            # not merged). Row lock serializes it (Postgres; no-op on sqlite,
            # which has no concurrent writers to race in the first place).
            skill: Skill | None = None
            if skill_id:
                skill = await session.get(Skill, skill_id, with_for_update=True)
            if skill is None and domain and capability:
                res = await session.execute(
                    select(Skill)
                    .where(Skill.domain == domain, Skill.capability == capability)
                    .with_for_update()
                )
                skill = res.scalars().first()
            if skill is None:
                return False
            evidence = list(skill.evidence or [])
            evidence.append(ev)
            skill.evidence = evidence  # reassign → JSON change-tracking
            await session.commit()
            return True
    except Exception as exc:  # best-effort, like events.emit
        logger.warning("skill self-eval evidence write failed: %s", exc)
        return False


async def _persist_last_failing_trace(
    config: dict[str, Any], trace: dict[str, Any]
) -> bool:
    """Best-effort: stash this run's full ``journey_trace_v1`` on
    ``skill.last_failing_trace`` (2026-07-01 addendum).

    ``self_eval``/``evidence`` only ever recorded a ``trace_id`` + outcome
    summary — never the trace body — so a human looking at a
    ``correction_proposed`` entry later (dock, days after the run) had no trace
    to actually pass to ``/redistill``. This overwrites with the *most recent*
    failing trace only (v1: same "keep it simple" call as
    ``correction.re_distill`` distilling only the latest trace). Same short-
    session + row-lock pattern as ``_append_self_eval``; never raises.
    """
    skill_id = config.get("skill_id")
    domain = config.get("domain")
    capability = config.get("capability")
    if not skill_id and not (domain and capability):
        return False
    try:
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.skill import Skill

        async with AsyncSessionLocal() as session:
            skill: Skill | None = None
            if skill_id:
                skill = await session.get(Skill, skill_id, with_for_update=True)
            if skill is None and domain and capability:
                res = await session.execute(
                    select(Skill)
                    .where(Skill.domain == domain, Skill.capability == capability)
                    .with_for_update()
                )
                skill = res.scalars().first()
            if skill is None:
                return False
            skill.last_failing_trace = trace
            await session.commit()
            return True
    except Exception as exc:  # best-effort, like events.emit
        logger.warning("skill last_failing_trace write failed: %s", exc)
        return False


async def _maybe_propose_correction(config: dict[str, Any]) -> bool:
    """Best-effort v2 auto-trigger: propose (never run) a re-distill after N
    consecutive execution failures (ADR-0003 D7 v2 addendum, grilled 2026-07-01).

    Opens its own short-lived ``AsyncSessionLocal()`` (same pattern as
    ``_append_self_eval`` — ``collect()`` holds no injected session), (re)loads
    the persisted :class:`~backend.models.skill.Skill` by ``skill_id`` or
    ``(domain, capability)``, and delegates the decision + evidence mutation to
    :func:`backend.skills.correction.maybe_propose_correction`. Commits only when
    a proposal was actually appended. Inline-skill case (no persisted row, like
    ``_append_self_eval``) is a no-op. Never raises.
    """
    skill_id = config.get("skill_id")
    domain = config.get("domain")
    capability = config.get("capability")
    if not skill_id and not (domain and capability):
        return False
    try:
        from sqlalchemy import select

        from backend.database import AsyncSessionLocal
        from backend.models.skill import Skill
        from backend.skills.correction import maybe_propose_correction

        async with AsyncSessionLocal() as session:
            # Row lock — same concurrency reasoning as _append_self_eval above.
            skill: Skill | None = None
            if skill_id:
                skill = await session.get(Skill, skill_id, with_for_update=True)
            if skill is None and domain and capability:
                res = await session.execute(
                    select(Skill)
                    .where(Skill.domain == domain, Skill.capability == capability)
                    .with_for_update()
                )
                skill = res.scalars().first()
            if skill is None:
                return False
            evidence = list(skill.evidence or [])
            proposed = maybe_propose_correction(skill, evidence)
            if proposed:
                skill.evidence = evidence  # reassign → JSON change-tracking
                await session.commit()
            return proposed
    except Exception as exc:  # best-effort, like events.emit
        logger.warning("skill correction proposal check failed: %s", exc)
        return False


@register_channel
class SkillChannel(AbstractChannel):
    """Execute a distilled browser skill via a cheap model over CDP."""

    channel_type = "skill"
    # Drives a real Chrome from the shared pool → must run on the node holding the
    # live session; the pipeline resolves a site-keyed browser binding for it.
    capabilities = Capabilities(session_affinity=True)

    async def collect(
        self, config: dict[str, Any], parameters: dict[str, Any]
    ) -> ChannelResult:
        skill_md, elements, identity, err = await _resolve_skill(config)
        if err:
            return ChannelResult.fail(err)

        run_id = parameters.get("run_id")
        task = parameters.get("task") or config.get("task") or ""
        # Cheap executor model (distinct from the distill model). Shape matches
        # backend.skills.distill provider config.
        provider = config.get("provider", {})
        model = provider.get("model") or "qwen3:4b"
        # Guardrail: writes (clicks/typing/submits) require explicit confirm
        # unless the source opts a trusted skill into unattended running.
        auto_confirm = bool(config.get("auto_confirm", False))

        try:
            model_call = await _build_model_call(provider)
        except ValueError as exc:
            return ChannelResult.fail(str(exc), error_type="SSRFValidationError")

        from backend.browser_pool import get_pool
        from backend.skills.loop import run_skill_loop  # lazy: breaks import cycle
        from backend.skills.page import open_skill_page

        pool = get_pool()
        endpoint = parameters.get("chrome_endpoint") or None

        try:
            async with pool.acquire(endpoint=endpoint) as cdp_endpoint:
                mode = pool.get_mode(cdp_endpoint)
                logger.info(
                    "skill channel | task=%r mode=%s cdp=%s model=%s confirm=%s run_id=%s",
                    task[:80], mode, cdp_endpoint, model, auto_confirm, run_id,
                )

                skill_page = await open_skill_page(cdp_endpoint)
                try:
                    page = _PerceivingPage(skill_page)
                    # Drive the perceive → gate → act loop (issues 03/04). The loop
                    # self-emits the awaiting_confirm event (it has run_id); per-step
                    # spine events are emitted by this channel below.
                    result = await run_skill_loop(
                        page=page,
                        model_call=model_call,
                        model=model,
                        skill_md=skill_md,
                        elements=elements,
                        task=task or None,
                        skill=elements or config,
                        auto_confirm=auto_confirm,
                        run_id=run_id,
                        emit=events.emit,
                    )
                finally:
                    await skill_page.aclose()

                # Emit per-step events (best-effort; no-op when no run_id).
                # AUDIT C24: one session + bulk insert + one commit for the
                # whole step trace, instead of one commit (fsync) per step.
                if run_id:
                    await events.emit_many(run_id, _emit_loop_events(result))

                items = _extracts_to_items(result)

                # ── Feedback leg (issue 06): journey_trace_v1 + self-eval ──────
                # Assemble the shared trace from the loop's own step records +
                # outcome, compute the self-eval vs the skill's terminal/milestone
                # conditions, and append it to skills.evidence when a persisted
                # Skill is resolvable (inline-skill case: best-effort skip).
                trace_id = run_id or f"skill-{uuid.uuid4().hex}"
                domain = identity.get("domain") or config.get("domain") or "unknown"
                label = (
                    identity.get("capability")
                    or config.get("capability")
                    or config.get("label")
                    or (task[:80] if task else "")
                    or "unknown"
                )
                outcome = outcome_from_loop(
                    result.outcome,
                    milestones_hit=_milestones_hit(result, elements),
                    terminal_check=_terminal_check(result),
                    extra={
                        "awaiting_confirm": bool(result.awaiting_confirm),
                        # 2026-07-01: grounds self_eval's terminal_met against
                        # the declared terminal_conditions text (was a no-op
                        # before — see _terminal_conditions_hit's docstring).
                        "terminal_conditions_hit": _terminal_conditions_hit(result, elements),
                    },
                )
                outcome["trace_id"] = trace_id
                trace = assemble_trace(
                    _step_records(result),
                    outcome,
                    domain=domain,
                    label=label,
                    trace_id=trace_id,
                    extra={"extract_count": len(result.extracts)},
                )
                # self_eval reads elements off a Skill row or a bare elements dict.
                ev = self_eval(outcome, elements or config)
                await _append_self_eval({**config, **identity}, ev)
                if not ev["passed"]:
                    # 2026-07-01 addendum: keep the full trace around so a human
                    # reviewing a later correction_proposed has something to
                    # actually redistill from (see _persist_last_failing_trace).
                    await _persist_last_failing_trace({**config, **identity}, trace)
                # v2 addendum (ADR-0003 D7): after logging the self-eval, check
                # whether it just completed an N-in-a-row fail streak — if so,
                # flag (never auto-run) a re-distill. See
                # backend.skills.correction.maybe_propose_correction.
                await _maybe_propose_correction({**config, **identity})

                metadata: dict[str, Any] = {
                    "channel": "skill",
                    "chrome_mode": mode,
                    "executed": True,
                    "outcome": result.outcome,
                    "trace": trace,
                    "self_eval": ev,
                    AWAITING_CONFIRM: bool(result.awaiting_confirm),
                }
                if result.awaiting_confirm and result.proposed_action is not None:
                    metadata[PROPOSED_ACTION] = result.proposed_action
                return ChannelResult.ok(items, **metadata)
        except Exception as exc:
            logger.error("skill channel | browser acquire/exec failed: %s", exc)
            return ChannelResult.fail(
                f"skill channel browser error: {exc}", error_type=type(exc).__name__
            )

    async def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("skill_md") and not config.get("skill_id") and not (
            config.get("domain") and config.get("capability")
        ):
            errors.append(
                "skill channel requires 'skill_md', or 'skill_id', or "
                "('domain' + 'capability')"
            )
        return errors
