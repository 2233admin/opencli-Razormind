"""全局 Agent 对话坞后端端点.

应用 Shell 的统一操作入口。用户用自然语言说话, agent (复用已有
provider/模型网关 + OpenAI tool-calling) 决定调工具:

  - 只读工具 (list_sources) 直接执行, 喂回结果让 agent 继续推理。
  - 写工具 (toggle_source) **不立即落库**, 返回一个 proposal 让前端弹 diff 确认。

确认后前端调 /chat/confirm, 这里才走统一 Agent Control 服务落库。写前确认是硬底线。

v1 薄闭环: 唯一写动作 = 启停 source。验证通后按同模式扩 trigger_task / update_schedule。
"""

import json
import logging
import re
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.control.agent_control import ACTION_REGISTRY, agent_control_service
from backend.database import get_db
from backend.models.provider import ModelProvider
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.services import schedule_service, source_service, task_service
from backend.skills.toolcall import _is_xml_tool_model, _parse_tool_use, _safe_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_TOOL_STEPS = 5

SYSTEM_PROMPT = """你是 opencli-admin 的全局操作助手。用户可能位于任意产品页面。\
你的职责: 根据当前页面和对象上下文解释系统状态，并在已有工具覆盖范围内按用户意图查询或修改后端配置。

规则:
- 需要知道有哪些数据源时, 调 list_sources。
- 用户要启用/停用某个数据源时, 调 toggle_source。这是写操作, 系统不会立即执行, 会先让用户确认。
- 用户要配置 AI 处理(富化)阶段时(换模型 / 开关 AI), 先 list_providers 看现有提供商, 再 update_provider。
  启用一个 provider = 采集成功后自动用它跑 AI 富化; 全部停用 = 不跑 AI。换模型改 default_model。
- 不要编造 id; 先用 list_* 拿到真实 id 再做写操作。
- 用中文简洁回答。"""


# ── 工具定义 (OpenAI function-calling schema) ───────────────────────────────
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sources",
            "description": "列出所有采集数据源 (返回 id / name / channel_type / enabled)。只读, 立即执行。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_source",
            "description": "启用或停用一个采集数据源。写操作, 不会立即生效, 会生成待用户确认的改动。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string", "description": "数据源 id"},
                    "enabled": {"type": "boolean", "description": "true=启用, false=停用"},
                },
                "required": ["source_id", "enabled"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_schedules",
            "description": "列出所有定时调度计划 (返回 id / name / cron_expression / enabled / source_id)。只读, 立即执行。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出最近的采集任务 (返回 id / source_id / status / trigger_type)。只读, 立即执行。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_task",
            "description": "对某个数据源立即触发一次采集运行。写操作, 需用户确认。source 必须已启用。",
            "parameters": {
                "type": "object",
                "properties": {"source_id": {"type": "string", "description": "数据源 id"}},
                "required": ["source_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_schedule",
            "description": "修改一个定时调度: 改 cron 表达式或启用/停用。写操作, 需用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "schedule_id": {"type": "string", "description": "调度 id"},
                    "cron_expression": {"type": "string", "description": "5 段 cron 表达式 (可选)"},
                    "enabled": {"type": "boolean", "description": "启用/停用 (可选)"},
                },
                "required": ["schedule_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_providers",
            "description": "列出所有模型提供商 (返回 id / name / provider_type / default_model / base_url / enabled)。AI 富化阶段用哪个模型由 provider 决定。只读, 立即执行。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_provider",
            "description": "配置 AI 处理阶段: 改某个模型提供商的默认模型, 或启用/停用它。启用一个 provider 后, 采集成功会自动用它跑 AI 富化; 全部停用则不跑 AI。写操作, 需用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "provider_id": {"type": "string", "description": "模型提供商 id"},
                    "default_model": {"type": "string", "description": "默认模型名 (可选, 如 qwen3:4b)"},
                    "enabled": {"type": "boolean", "description": "启用/停用 (可选)"},
                },
                "required": ["provider_id"],
            },
        },
    },
]

WRITE_TOOLS = ACTION_REGISTRY.action_names


async def _optional_request_identity(request: Request) -> RequestIdentity | None:
    """Preserve unauthenticated read-chat compatibility; writes still fail closed."""

    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return await get_request_identity(request)


def _require_write_identity(identity: RequestIdentity | None) -> RequestIdentity:
    if identity is None:
        raise HTTPException(status_code=401, detail="Bearer token required for write proposals")
    return identity


# ── request / response 模型 ─────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider_id: Optional[str] = None
    # 当前页面、项目或选中对象上下文，注入给 agent 当指代背景
    context: Optional[dict[str, Any]] = None


class Proposal(BaseModel):
    tool: str
    args: dict[str, Any]
    summary: str
    diff: str
    work_item_id: Optional[str] = None
    workspace_id: Optional[str] = None
    proposal_version: Optional[str] = None


class ChatReply(BaseModel):
    type: Literal["message", "proposal"]
    content: Optional[str] = None
    proposal: Optional[Proposal] = None


class ConfirmRequest(BaseModel):
    proposal: Proposal


# ── provider → AsyncOpenAI client ───────────────────────────────────────────
async def _pick_provider(db: AsyncSession, provider_id: Optional[str]) -> ModelProvider:
    if provider_id:
        provider = await db.get(ModelProvider, provider_id)
        if not provider or not provider.enabled:
            raise HTTPException(status_code=400, detail="指定的模型 provider 不存在或未启用")
        return provider
    result = await db.execute(
        select(ModelProvider).where(ModelProvider.enabled.is_(True)).order_by(ModelProvider.created_at.asc())
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(status_code=400, detail="没有可用的模型 provider, 先在「模型提供商」里配置一个并启用")
    return provider


async def _build_client(provider: ModelProvider):
    """Build the agent dock's OpenAI-compatible tool-calling client.

    GOAL-6 PR-E: consolidates what used to be a private ``AsyncOpenAI(...)``
    construction here into :class:`~backend.llm.openai_compat.OpenAICompatAdapter`
    via :func:`~backend.llm.factory.build_openai_compat_adapter` — the same
    guarded client :class:`OpenAICompatAdapter` gives every other PR-E
    consumer, so this file stops duplicating the SSRF-guard + DNS-rebind-
    pinning wiring. The tool-calling loop below stays exactly as it was
    (needs the *raw* client for ``tools=``/``tool_choice=``, which the
    adapter's thin ``chat()`` doesn't support) — only client *construction*
    moves.

    Preserved exactly: the ``OPENAI_API_KEY`` env fallback when the selected
    provider has no ``api_key`` configured, and this file's pre-existing
    behavior of treating ANY selected provider (regardless of
    ``provider_type``) as an OpenAI-compatible endpoint — ``_pick_provider``
    never filtered by ``provider_type``, so neither does this.

    Deliberate, narrow behavior change (decision #6): the previous
    ``_build_client`` had NO SSRF guard at all. Routing through
    ``OpenAICompatAdapter`` now validates ``provider.base_url`` before
    attaching the api_key to a client pointed at it — closing an SSRF/key-
    exfil gap that already existed everywhere else (openai_processor,
    skill_channel) but not here. No existing test exercises this path (see
    ``tests/integration/test_chat_api.py``'s docstring: "the LLM round trip
    itself is out of scope"), so this cannot regress the test suite; a
    provider whose base_url fails the guard now gets a clear 502 instead of
    an unguarded outbound call.
    """
    try:
        from openai import AsyncOpenAI  # noqa: F401 -- import-availability probe only
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="openai package not installed") from exc
    import os

    from backend.llm.base import LlmAdapterError
    from backend.llm.factory import build_openai_compat_adapter

    api_key = provider.api_key or os.environ.get("OPENAI_API_KEY", "")
    adapter = build_openai_compat_adapter(base_url=provider.base_url, api_key=api_key)
    try:
        return await adapter.get_client()
    except LlmAdapterError as exc:
        raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc


# ── 只读工具执行 ─────────────────────────────────────────────────────────────
async def _run_read_tool(db: AsyncSession, name: str, args: dict[str, Any]) -> Any:
    if name == "list_sources":
        sources, _ = await source_service.list_sources(db, page=1, limit=100)
        return [
            {"id": s.id, "name": s.name, "channel_type": s.channel_type, "enabled": s.enabled}
            for s in sources
        ]
    if name == "list_schedules":
        schedules, _ = await schedule_service.list_schedules(db, page=1, limit=100)
        return [
            {"id": s.id, "name": s.name, "cron_expression": s.cron_expression, "enabled": s.enabled, "source_id": s.source_id}
            for s in schedules
        ]
    if name == "list_tasks":
        tasks, _ = await task_service.list_tasks(db, page=1, limit=30)
        return [
            {"id": t.id, "source_id": t.source_id, "status": t.status, "trigger_type": t.trigger_type}
            for t in tasks
        ]
    if name == "list_providers":
        result = await db.execute(select(ModelProvider).order_by(ModelProvider.created_at.asc()))
        return [
            {
                "id": p.id, "name": p.name, "provider_type": p.provider_type,
                "default_model": p.default_model, "base_url": p.base_url, "enabled": p.enabled,
            }
            for p in result.scalars().all()
        ]
    return {"error": f"unknown read tool: {name}"}


def _workspace_id(context: dict[str, Any] | None) -> str | None:
    if not context:
        return None
    value = context.get("workspace_id")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


async def _build_proposal(
    db: AsyncSession,
    name: str,
    args: dict[str, Any],
    *,
    identity: RequestIdentity | None = None,
    workspace_id: str | None = None,
) -> Proposal:
    """Preview an action and, for authenticated transports, persist its proposal."""

    if identity is None:
        # Kept for internal callers that only need the existing preview shape.
        preview = await agent_control_service.preview(db, name, args)
        return Proposal(
            tool=preview.action_name,
            args=preview.args,
            summary=preview.summary,
            diff=preview.diff,
        )

    resolved_workspace_id = await agent_control_service.resolve_workspace_id(
        db,
        identity,
        workspace_id,
    )
    recorded = await agent_control_service.create_proposal(
        db,
        workspace_id=resolved_workspace_id,
        identity=identity,
        action_name=name,
        args=args,
        origin="chat",
    )
    return Proposal(
        tool=recorded.preview.action_name,
        args=recorded.preview.args,
        summary=recorded.preview.summary,
        diff=recorded.preview.diff,
        work_item_id=recorded.work_item_id,
        workspace_id=recorded.workspace_id,
        proposal_version=recorded.proposal_version,
    )


@router.post("", response_model=ApiResponse[ChatReply])
async def chat(
    body: ChatRequest,
    identity: RequestIdentity | None = Depends(_optional_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    provider = await _pick_provider(db, body.provider_id)
    client = await _build_client(provider)
    model = provider.default_model or "gpt-4o-mini"

    system = SYSTEM_PROMPT
    if body.context:
        system += f"\n\n当前用户操作上下文 (JSON): {json.dumps(body.context, ensure_ascii=False)}"

    if _is_xml_tool_model(model):
        return await _chat_xml(client, model, system, body, db, identity)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages]

    for _step in range(MAX_TOOL_STEPS):
        try:
            response = await client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice="auto"
            )
        except Exception as exc:
            logger.error("chat llm error | %s", exc)
            raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []

        if not tool_calls:
            return ApiResponse.ok(ChatReply(type="message", content=msg.content or ""))

        # 写工具命中 → 立即返回 proposal (不执行, 不继续推理)
        for tc in tool_calls:
            if tc.function.name in WRITE_TOOLS:
                args = _safe_json(tc.function.arguments)
                proposal = await _build_proposal(
                    db,
                    tc.function.name,
                    args,
                    identity=_require_write_identity(identity),
                    workspace_id=_workspace_id(body.context),
                )
                return ApiResponse.ok(ChatReply(type="proposal", proposal=proposal))

        # 只读工具 → 执行, 喂回结果, 继续循环
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            result = await _run_read_tool(db, tc.function.name, _safe_json(tc.function.arguments))
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)}
            )

    return ApiResponse.ok(ChatReply(type="message", content="(达到工具调用步数上限, 请换个说法再试)"))


@router.post("/confirm", response_model=ApiResponse[dict])
async def confirm(
    body: ConfirmRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Execute a proposal only through the confirmed Agent Control path."""
    proposal = body.proposal
    workspace_id = await agent_control_service.resolve_workspace_id(
        db,
        identity,
        proposal.workspace_id,
    )
    work_item_id = proposal.work_item_id
    proposal_version = proposal.proposal_version

    if (work_item_id is None) != (proposal_version is None):
        raise HTTPException(
            status_code=409,
            detail="Agent Control proposal metadata is incomplete",
        )
    if work_item_id is None:
        # Compatibility for clients that still send the original Proposal
        # shape. The confirmation endpoint itself is the explicit gate, so
        # persist the governed proposal immediately before executing it.
        recorded = await agent_control_service.create_proposal(
            db,
            workspace_id=workspace_id,
            identity=identity,
            action_name=proposal.tool,
            args=proposal.args,
            origin="chat.confirm.compat",
        )
        work_item_id = recorded.work_item_id
        proposal_version = recorded.proposal_version

    assert work_item_id is not None
    assert proposal_version is not None
    result = await agent_control_service.execute_confirmed(
        db,
        workspace_id=workspace_id,
        identity=identity,
        work_item_id=work_item_id,
        proposal_version=proposal_version,
        confirmation_path="chat.confirm",
        expected_action=proposal.tool,
    )
    return ApiResponse.ok(result)


# ── XML-style tool models (e.g. Qwable-v1: emits <tool_use> XML, not OpenAI tool_calls) ──
# XML tool-call parsing (XML_TOOL_MODELS / _TOOL_USE_RE / _is_xml_tool_model /
# _parse_tool_use / _safe_json) is shared with the skill execute core — the single
# source of truth lives in backend.skills.toolcall (imported above). Qwable-v1
# emits custom <tool_use name="X">{json}</tool_use> in the message content instead
# of OpenAI tool_calls; we describe the tools in the system prompt as text
# (XML_TOOL_TEXT) and parse the XML ourselves via the imported helpers.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

XML_TOOL_TEXT = (
    "\n\n你是采集网络操作 agent。可用工具:\n"
    "- list_sources(): 列出所有数据源 (id/name/enabled)。\n"
    "- list_schedules(): 列出定时调度 (id/name/cron_expression/enabled)。\n"
    "- list_tasks(): 列出最近采集任务 (id/source_id/status)。\n"
    "- toggle_source(source_id, enabled): 启用/停用数据源 (写)。\n"
    "- trigger_task(source_id): 立即触发一次采集 (写)。\n"
    "- update_schedule(schedule_id, cron_expression?, enabled?): 改调度 cron 或启停 (写)。\n"
    "- list_providers(): 列出模型提供商 (id/name/default_model/enabled)。\n"
    "- update_provider(provider_id, default_model?, enabled?): 配置 AI 富化阶段的模型提供商, 改模型或启停 (写)。\n"
    '需要调用工具时, 严格输出 XML: <tool_use name="工具名" id="toolu_1">{json 参数}</tool_use>\n'
    "先用 list_* 拿到真实 id 再做写操作。不要用 markdown 代码块。"
)


async def _chat_xml(
    client: Any,
    model: str,
    system: str,
    body: ChatRequest,
    db: AsyncSession,
    identity: RequestIdentity | None,
) -> ApiResponse:
    """Tool loop for XML-style models (parse <tool_use> from content, feed results back as text)."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system + XML_TOOL_TEXT}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages]

    for _step in range(MAX_TOOL_STEPS):
        try:
            response = await client.chat.completions.create(model=model, messages=messages, max_tokens=1024)
        except Exception as exc:
            logger.error("chat(xml) llm error | %s", exc)
            raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc

        content = response.choices[0].message.content or ""
        calls = _parse_tool_use(content)

        if not calls:
            clean = _THINK_RE.sub("", content).strip()
            return ApiResponse.ok(ChatReply(type="message", content=clean or "(无内容)"))

        # write tool hit → return proposal immediately
        for name, args in calls:
            if name in WRITE_TOOLS:
                proposal = await _build_proposal(
                    db,
                    name,
                    args,
                    identity=_require_write_identity(identity),
                    workspace_id=_workspace_id(body.context),
                )
                return ApiResponse.ok(ChatReply(type="proposal", proposal=proposal))

        # read tools → execute, feed results back as <tool_result> text, loop
        messages.append({"role": "assistant", "content": content})
        for name, args in calls:
            result = await _run_read_tool(db, name, args)
            messages.append(
                {"role": "user", "content": f'<tool_result name="{name}">{json.dumps(result, ensure_ascii=False)}</tool_result>'}
            )

    return ApiResponse.ok(ChatReply(type="message", content="(达到工具调用步数上限, 请换个说法再试)"))
