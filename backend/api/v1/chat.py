"""全局 Agent 对话坞后端端点.

应用 Shell 的统一操作入口。用户用自然语言说话, agent (复用已有
provider/模型网关 + OpenAI tool-calling) 决定调工具:

  - 只读工具 (list_sources) 直接执行, 喂回结果让 agent 继续推理。
  - 写工具 (toggle_source) **不立即落库**, 返回一个 proposal 让前端弹 diff 确认。

确认后前端调 /chat/confirm, 这里才走统一 Agent Control 服务落库。写前确认是硬底线。

v1 薄闭环: 唯一写动作 = 启停 source。验证通后按同模式扩 trigger_task / update_schedule。
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.studio_helpers import canonicalize_studio_graph
from backend.control.agent_control import (
    ACTION_REGISTRY,
    ProposalProvenance,
    agent_control_service,
)
from backend.database import AsyncSessionLocal, get_db
from backend.llm.base import LlmAdapterError, classify_retryable
from backend.llm.resolver import ResolverError, resolver
from backend.models.agent_run import AgentRun, AgentRunEvent, AgentSession
from backend.models.provider import ModelProvider
from backend.models.provider_model import ProviderModel
from backend.schemas import workflow as workflow_schemas
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity
from backend.security.workspace_rbac import (
    WorkspacePermission,
    get_workspace_access,
    require_permission,
)
from backend.services import agent_project_service, schedule_service, source_service, task_service
from backend.skills.toolcall import _is_xml_tool_model, _parse_tool_use, _safe_json

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

MAX_TOOL_STEPS = 5
XML_TOOL_MAX_TOKENS = 4096

ActivitySink = Callable[[dict[str, Any]], Awaitable[None]]
ActivityFlusher = Callable[[], Awaitable[None]]
_activity_sink: ContextVar[ActivitySink | None] = ContextVar("chat_activity_sink", default=None)
_activity_flusher: ContextVar[ActivityFlusher | None] = ContextVar(
    "chat_activity_flusher", default=None
)
_background_runs: set[asyncio.Task] = set()

_PUBLIC_TOOL_LABELS = {
    "list_sources": ("检查数据源", "数据源"),
    "list_schedules": ("检查调度计划", "调度计划"),
    "list_tasks": ("检查最近任务", "采集任务"),
    "list_providers": ("检查模型连接", "模型提供商"),
    "toggle_source": ("变更数据源状态", "数据源"),
    "trigger_task": ("启动采集任务", "数据源"),
    "update_schedule": ("更新调度计划", "调度计划"),
    "update_provider": ("更新模型配置", "模型提供商"),
    "list_projects": ("检查项目", "项目"),
    "list_workflows": ("检查工作流", "工作流"),
    "get_workflow_draft": ("读取工作流草稿", "工作流草稿"),
    "create_project": ("创建项目草稿", "项目"),
    "update_workflow_draft": ("更新工作流草稿", "工作流草稿"),
    "validate_workflow_draft": ("验证工作流草稿", "工作流草稿"),
    "publish_workflow": ("发布工作流版本", "工作流版本"),
    "run_managed_doubao_question": ("运行受管豆包单题", "工作流运行"),
}


async def _emit_activity(event_type: str, label: str, detail: str, **extra: Any) -> None:
    sink = _activity_sink.get()
    if sink is not None:
        await sink({"type": event_type, "label": label, "detail": detail, **extra})


async def _flush_activity() -> None:
    flusher = _activity_flusher.get()
    if flusher is not None:
        await flusher()


def _tool_public_description(name: str, args: dict[str, Any]) -> tuple[str, str, str | None]:
    label, target_type = _PUBLIC_TOOL_LABELS.get(name, ("执行操作", "系统对象"))
    target_id = next(
        (
            str(args[key])
            for key in ("workflow_id", "project_id", "source_id", "schedule_id", "provider_id")
            if args.get(key)
        ),
        None,
    )
    return label, target_type, target_id


def _result_public_summary(result: Any) -> str:
    if isinstance(result, list):
        return f"找到 {len(result)} 项可用信息"
    if isinstance(result, dict) and result.get("error"):
        return "未能读取目标信息"
    return "已读取目标信息"


SYSTEM_PROMPT = """你是 opencli-admin 的全局操作助手。用户可能位于任意产品页面。\
你的职责: 根据当前页面和对象上下文解释系统状态，并在已有工具覆盖范围内按用户意图查询或修改后端配置。

规则:
- 需要知道有哪些数据源时, 调 list_sources。
- 用户要启用/停用某个数据源时, 调 toggle_source。这是写操作, 系统不会立即执行, 会先让用户确认。
- 用户要配置 AI 处理(富化)阶段时(换模型 / 开关 AI), 先 list_providers 看现有提供商,
  再 update_provider。
  启用一个 provider = 采集成功后自动用它跑 AI 富化; 全部停用 = 不跑 AI。换模型改 default_model。
- 用户要查看当前工作区的项目、工作流或草稿时，依次用 list_projects、list_workflows、
  get_workflow_draft。工作区由服务端绑定，不要向用户索取 workspace_id。
- 用户要创建项目时，用 create_project 创建 Project、主 Workflow 和 revision=1 的 Draft；
  用户要改草稿时，用 update_workflow_draft，并使用读取到的当前 revision。
- create_project 和 update_workflow_draft 都是写操作，只生成待确认提案；
  草稿不等于已发布或可运行版本。
- 用户要把草稿变成可运行版本时，先 validate_workflow_draft，再 publish_workflow；
  每一步都是独立的待确认提案，必须使用当前 revision 和 validation id。
- 用户明确要求运行豆包采集时，只能用 run_managed_doubao_question 运行一个不超过
  1000 字的问题，并绑定当前已发布版本。这是待确认提案；运行前会重新检查豆包会话
  可用性。OpenCLI 报告 Login=Unknown 时保留该不确定性，不宣称已经登录。
- 不要编造 id; 先用 list_* 拿到真实 id 再做写操作。
- 用中文简洁回答。"""


# ── 工具定义 (OpenAI function-calling schema) ───────────────────────────────
_WORKFLOW_PROJECT_TOOL_SCHEMA = workflow_schemas.WorkflowProject.model_json_schema()
_WORKFLOW_PROJECT_DEFS = _WORKFLOW_PROJECT_TOOL_SCHEMA.pop("$defs", {})

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "list_sources",
            "description": "列出所有采集数据源 (返回 id / name / channel_type / enabled)。只读, 立即执行。",  # noqa: E501
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "toggle_source",
            "description": "启用或停用一个采集数据源。写操作, 不会立即生效, 会生成待用户确认的改动。",  # noqa: E501
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
            "description": "列出所有定时调度计划 (返回 id / name / cron_expression / enabled / source_id)。只读, 立即执行。",  # noqa: E501
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_tasks",
            "description": "列出最近的采集任务 (返回 id / source_id / status / trigger_type)。只读, 立即执行。",  # noqa: E501
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_task",
            "description": "对某个数据源立即触发一次采集运行。写操作, 需用户确认。source 必须已启用。",  # noqa: E501
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
            "description": "列出所有模型提供商 (返回 id / name / provider_type / default_model / base_url / enabled)。AI 富化阶段用哪个模型由 provider 决定。只读, 立即执行。",  # noqa: E501
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_provider",
            "description": "配置 AI 处理阶段: 改某个模型提供商的默认模型, 或启用/停用它。启用一个 provider 后, 采集成功会自动用它跑 AI 富化; 全部停用则不跑 AI。写操作, 需用户确认。",  # noqa: E501
            "parameters": {
                "type": "object",
                "properties": {
                    "provider_id": {"type": "string", "description": "模型提供商 id"},
                    "default_model": {
                        "type": "string",
                        "description": "默认模型名 (可选, 如 qwen3:4b)",
                    },
                    "enabled": {"type": "boolean", "description": "启用/停用 (可选)"},
                },
                "required": ["provider_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_projects",
            "description": "列出当前服务端绑定 Workspace 的 Studio 项目。只读。",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_workflows",
            "description": "列出当前 Workspace 中指定 Project 的工作流。只读。",
            "parameters": {
                "type": "object",
                "properties": {"project_id": {"type": "string"}},
                "required": ["project_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_workflow_draft",
            "description": "读取指定 Studio 工作流的当前草稿图和 revision。只读，不代表已发布。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                },
                "required": ["project_id", "workflow_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_project",
            "description": (
                "创建 Studio Project、主 Workflow 和 revision=1 的 Draft。"
                "写操作，需确认；不会发布或运行。"
            ),
            "parameters": {
                "type": "object",
                "$defs": _WORKFLOW_PROJECT_DEFS,
                "properties": {
                    "project": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "slug": {"type": "string"},
                            "description": {"type": "string"},
                            "app_type": {
                                "type": "string",
                                "enum": [
                                    "chatbot",
                                    "agent",
                                    "chatflow",
                                    "workflow",
                                    "text-generator",
                                ],
                            },
                        },
                        "required": ["name", "slug"],
                        "additionalProperties": False,
                    },
                    "workflow": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "description": {"type": "string"},
                            "graph": _WORKFLOW_PROJECT_TOOL_SCHEMA,
                        },
                        "required": ["name", "graph"],
                        "additionalProperties": False,
                    },
                },
                "required": ["project", "workflow"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_workflow_draft",
            "description": (
                "按当前 revision 更新 Studio Workflow Draft。写操作，需确认；不会发布或运行。"
            ),
            "parameters": {
                "type": "object",
                "$defs": _WORKFLOW_PROJECT_DEFS,
                "properties": {
                    "project_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "revision": {"type": "integer", "minimum": 1},
                    "graph": _WORKFLOW_PROJECT_TOOL_SCHEMA,
                },
                "required": ["project_id", "workflow_id", "revision", "graph"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "validate_workflow_draft",
            "description": (
                "验证指定 Studio 工作流当前草稿 revision。写操作，需确认；不会发布或运行。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                },
                "required": ["project_id", "workflow_id", "expected_revision"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "publish_workflow",
            "description": "发布已经通过验证的精确工作流草稿版本。写操作，需确认；不会运行。",
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "expected_revision": {"type": "integer", "minimum": 1},
                    "validation_run_id": {"type": "string"},
                    "expected_current_published_version": {
                        "type": ["integer", "null"],
                        "minimum": 1,
                    },
                    "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                },
                "required": [
                    "project_id",
                    "workflow_id",
                    "expected_revision",
                    "validation_run_id",
                    "expected_current_published_version",
                    "reason",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_managed_doubao_question",
            "description": (
                "使用当前已发布且受管的高吉星豆包工作流运行一个真实问题。"
                "写操作，需确认；确认时重新检查权限、版本和豆包会话可用性。"
                "如果 OpenCLI 返回 Login=Unknown，结果仍保留该不确定性。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "workflow_id": {"type": "string"},
                    "expected_published_version": {"type": "integer", "minimum": 1},
                    "question": {"type": "string", "minLength": 1, "maxLength": 1000},
                },
                "required": [
                    "project_id",
                    "workflow_id",
                    "expected_published_version",
                    "question",
                ],
                "additionalProperties": False,
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


def _require_workspace_identity(identity: RequestIdentity | None) -> RequestIdentity:
    if identity is None:
        raise HTTPException(status_code=401, detail="Bearer token required for Workspace reads")
    return identity


# ── request / response 模型 ─────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    provider_id: str | None = None
    model_id: str | None = None
    session_id: str | None = None
    workspace_id: str | None = None
    context: dict[str, Any] | None = None


class Proposal(BaseModel):
    tool: str
    args: dict[str, Any]
    summary: str
    diff: str
    work_item_id: str | None = None
    workspace_id: str | None = None
    proposal_version: str | None = None


class ChatReply(BaseModel):
    type: Literal["message", "proposal"]
    content: str | None = None
    proposal: Proposal | None = None


@dataclass(frozen=True)
class ChatExecution:
    reply: ChatReply
    tool_trace: list[dict[str, Any]]


class ConfirmRequest(BaseModel):
    proposal: Proposal


async def _create_durable_run(body: ChatRequest, identity: RequestIdentity | None) -> AgentRun:
    """Create a durable run before work begins so clients can reconnect immediately."""
    async with AsyncSessionLocal() as session:
        agent_session: AgentSession | None = None
        if body.session_id:
            agent_session = await session.get(AgentSession, body.session_id)
            if agent_session is not None:
                if identity is None:
                    raise HTTPException(
                        status_code=401,
                        detail="Bearer token required for session reuse",
                    )
                if agent_session.actor_subject not in (None, identity.subject):
                    raise HTTPException(
                        status_code=403,
                        detail="Agent session belongs to another identity",
                    )
                if body.workspace_id and agent_session.workspace_id != body.workspace_id:
                    raise HTTPException(
                        status_code=403,
                        detail="Agent session belongs to another workspace",
                    )
                if agent_session.actor_subject is None:
                    agent_session.actor_subject = identity.subject
        if agent_session is None:
            agent_session = AgentSession(
                workspace_id=body.workspace_id or _workspace_id(body.context),
                actor_subject=identity.subject if identity else None,
                context=body.context or {},
            )
            session.add(agent_session)
            await session.flush()
        goal = next(
            (message.content for message in reversed(body.messages) if message.role == "user"), ""
        )
        run = AgentRun(
            session_id=agent_session.id,
            status="queued",
            goal=goal,
            request_payload={
                "messages": [message.model_dump() for message in body.messages],
                "context": body.context or {},
                "provider_id": body.provider_id,
                "model_id": body.model_id,
            },
        )
        session.add(run)
        await session.commit()
        await session.refresh(run)
        return run


class _DurableEventPersistenceError(RuntimeError):
    """The run-scoped writer could not commit its pending durable events."""


class _RunScopedDurableEventWriter:
    """Persist ordered public events in natural run batches before publishing them."""

    def __init__(self, run_id: str, queue: asyncio.Queue[dict[str, Any] | None]):
        self.run_id = run_id
        self.queue = queue
        self.session: AsyncSession | None = None
        self.run: AgentRun | None = None
        self.next_sequence = 1
        self.pending: list[dict[str, Any]] = []
        self.dirty = False

    async def __aenter__(self) -> "_RunScopedDurableEventWriter":
        self.session = AsyncSessionLocal()
        self.run = await self.session.get(AgentRun, self.run_id)
        if self.run is None:
            await self.session.close()
            raise RuntimeError("Agent run disappeared")
        self.next_sequence = self.run.next_event_sequence
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        if self.session is not None:
            await self.session.close()

    def mark_running(self) -> None:
        if self.run is None:
            raise RuntimeError("Durable event writer is not open")
        self.run.status = "running"
        self.dirty = True

    async def emit(self, event: dict[str, Any]) -> None:
        if self.session is None or self.run is None:
            raise RuntimeError("Durable event writer is not open")
        payload = {"sequence": self.next_sequence, **event}
        self.next_sequence += 1
        self.run.next_event_sequence = self.next_sequence
        self.session.add(
            AgentRunEvent(
                run_id=self.run.id,
                sequence=payload["sequence"],
                event_type=event["type"],
                payload=payload,
            )
        )
        self.pending.append(payload)
        self.dirty = True

    async def flush(self) -> None:
        if self.session is None or not self.dirty:
            return
        try:
            await self.session.commit()
        except Exception as exc:
            try:
                await self.session.rollback()
            except Exception:
                logger.exception("chat durable event rollback failed")
            finally:
                self.pending.clear()
                self.dirty = False
            raise _DurableEventPersistenceError("Failed to persist agent run events") from exc
        committed, self.pending = self.pending, []
        self.dirty = False
        for payload in committed:
            await self.queue.put(payload)

    async def finish(
        self,
        *,
        reply: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self.run is None:
            raise RuntimeError("Durable event writer is not open")
        self.run.status = "failed" if error else "completed"
        self.run.reply_payload = reply
        self.run.error_message = error
        self.dirty = True
        await self.flush()


async def _persist_writer_failure(
    run_id: str,
    queue: asyncio.Queue[dict[str, Any] | None],
) -> None:
    """Atomically persist a terminal event with a fresh database session."""

    async with AsyncSessionLocal() as session:
        run = await session.get(AgentRun, run_id, with_for_update=True)
        if run is None:
            raise RuntimeError("Agent run disappeared")
        payload = {
            "sequence": run.next_event_sequence,
            "type": "run.failed",
            "label": "处理未完成",
            "detail": "Agent 无法保存这次任务的执行进度。",
            "state": "failed",
            "status": 500,
            "recovery": "稍后重试；本次失败状态已保存。",
        }
        run.next_event_sequence += 1
        run.status = "failed"
        run.reply_payload = None
        run.error_message = "Agent event persistence failed"
        session.add(
            AgentRunEvent(
                run_id=run.id,
                sequence=payload["sequence"],
                event_type=payload["type"],
                payload=payload,
            )
        )
        await session.commit()

    await queue.put(payload)


# ── provider → AsyncOpenAI client ───────────────────────────────────────────
async def _pick_provider(db: AsyncSession, provider_id: str | None) -> ModelProvider:
    if provider_id:
        provider = await db.get(ModelProvider, provider_id)
        if not provider or not provider.enabled:
            raise HTTPException(status_code=400, detail="指定的模型 provider 不存在或未启用")
        return provider
    result = await db.execute(
        select(ModelProvider)
        .where(ModelProvider.enabled.is_(True))
        .order_by(ModelProvider.created_at.asc())
    )
    provider = result.scalars().first()
    if not provider:
        raise HTTPException(
            status_code=400,
            detail="没有可用的模型 provider, 先在「模型提供商」里配置一个并启用",
        )
    return provider


async def _build_client(provider: ModelProvider):
    """Build the agent dock's OpenAI-compatible tool-calling client.

    model-provider runtime PR-E: consolidates what used to be a private ``AsyncOpenAI(...)``
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
async def _run_read_tool(
    db: AsyncSession,
    name: str,
    args: dict[str, Any],
    *,
    identity: RequestIdentity | None = None,
    workspace_id: str | None = None,
    proposal_provenance: ProposalProvenance | None = None,
) -> Any:
    if name == "list_sources":
        sources, _ = await source_service.list_sources(db, page=1, limit=100)
        return [
            {"id": s.id, "name": s.name, "channel_type": s.channel_type, "enabled": s.enabled}
            for s in sources
        ]
    if name == "list_schedules":
        schedules, _ = await schedule_service.list_schedules(db, page=1, limit=100)
        return [
            {
                "id": s.id,
                "name": s.name,
                "cron_expression": s.cron_expression,
                "enabled": s.enabled,
                "source_id": s.source_id,
            }
            for s in schedules
        ]
    if name == "list_tasks":
        tasks, _ = await task_service.list_tasks(db, page=1, limit=30)
        return [
            {
                "id": t.id,
                "source_id": t.source_id,
                "status": t.status,
                "trigger_type": t.trigger_type,
            }
            for t in tasks
        ]
    if name == "list_providers":
        result = await db.execute(select(ModelProvider).order_by(ModelProvider.created_at.asc()))
        return [
            {
                "id": p.id,
                "name": p.name,
                "provider_type": p.provider_type,
                "default_model": p.default_model,
                "base_url": p.base_url,
                "enabled": p.enabled,
            }
            for p in result.scalars().all()
        ]
    if name in {"list_projects", "list_workflows", "get_workflow_draft"}:
        scoped_identity = _require_workspace_identity(identity)
        resolved_workspace_id = await agent_control_service.resolve_workspace_id(
            db,
            scoped_identity,
            workspace_id,
        )
        access = await get_workspace_access(db, resolved_workspace_id, scoped_identity)
        require_permission(access, WorkspacePermission.READ)
        resource_workspace_id = await agent_control_service.resolve_resource_workspace_id(
            db,
            workspace_id=resolved_workspace_id,
            identity=scoped_identity,
            provenance=proposal_provenance,
        )
        if name == "list_projects":
            projects = await agent_project_service.list_projects(
                db,
                workspace_id=resource_workspace_id,
            )
            return [
                {
                    "id": project.id,
                    "workspace_id": project.workspace_id,
                    "name": project.name,
                    "slug": project.slug,
                    "description": project.description,
                    "app_type": project.app_type,
                    "primary_workflow_id": project.primary_workflow_id,
                }
                for project in projects
            ]
        project_id = args.get("project_id")
        if not isinstance(project_id, str) or not project_id.strip():
            raise HTTPException(status_code=422, detail="project_id is required")
        if name == "list_workflows":
            workflows = await agent_project_service.list_workflows(
                db,
                workspace_id=resource_workspace_id,
                project_id=project_id,
            )
            return [
                {
                    "id": workflow.id,
                    "project_id": workflow.project_id,
                    "name": workflow.name,
                    "description": workflow.description,
                    "current_published_version": workflow.current_published_version,
                }
                for workflow in workflows
            ]
        workflow_id = args.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise HTTPException(status_code=422, detail="workflow_id is required")
        draft = await agent_project_service.get_workflow_draft(
            db,
            workspace_id=resource_workspace_id,
            project_id=project_id,
            workflow_id=workflow_id,
        )
        return {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "revision": draft.revision,
            "graph": canonicalize_studio_graph(draft.graph, workflow_id=workflow_id),
            "is_published_version": False,
            "updated_at": draft.updated_at.isoformat(),
        }
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
    provenance: ProposalProvenance | None = None,
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
        origin="agent_conversation" if provenance is not None else "chat",
        provenance=provenance,
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


async def _chat_with_client(
    client: Any,
    model: str,
    body: ChatRequest,
    db: AsyncSession,
    identity: RequestIdentity | None,
    *,
    tool_trace: list[dict[str, Any]] | None = None,
    proposal_provenance: ProposalProvenance | None = None,
) -> ChatExecution:
    """Run either provider protocol while returning a persistence-safe tool trace."""
    await _emit_activity(
        "phase.changed",
        "制定执行路径",
        "已选择可用模型，正在判断需要读取的信息和可能的操作。",
        state="active",
    )
    system = SYSTEM_PROMPT
    if body.context:
        system += f"\n\n当前用户操作上下文 (JSON): {json.dumps(body.context, ensure_ascii=False)}"

    if _is_xml_tool_model(model):
        return await _chat_xml(
            client,
            model,
            system,
            body,
            db,
            identity,
            tool_trace=tool_trace,
            proposal_provenance=proposal_provenance,
        )

    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages]
    tool_trace = tool_trace if tool_trace is not None else []

    for _step in range(MAX_TOOL_STEPS):
        await _emit_activity(
            "phase.changed",
            "分析当前状态",
            "正在根据已获得的信息决定下一步。",
            state="active",
        )
        await db.commit()
        try:
            response = await client.chat.completions.create(
                model=model, messages=messages, tools=TOOLS, tool_choice="auto"
            )
        except Exception as exc:
            logger.error("chat llm error | %s", exc)
            raise LlmAdapterError(
                f"模型调用失败: {exc}",
                retryable=classify_retryable(exc),
            ) from exc

        msg = response.choices[0].message
        tool_calls = msg.tool_calls or []
        if not tool_calls:
            await _emit_activity(
                "run.completed",
                "处理完成",
                "已生成基于本次执行信息的结果摘要。",
                state="completed",
            )
            return ChatExecution(ChatReply(type="message", content=msg.content or ""), tool_trace)

        for tc in tool_calls:
            if tc.function.name in WRITE_TOOLS:
                args = _safe_json(tc.function.arguments)
                label, target_type, target_id = _tool_public_description(tc.function.name, args)
                tool_trace.append(
                    {
                        "name": tc.function.name,
                        "kind": "write",
                        "status": "proposal",
                        "argument_keys": sorted(args),
                    }
                )
                await _emit_activity(
                    "tool.completed",
                    label,
                    "已定位目标并准备变更方案。",
                    state="completed",
                    target={"type": target_type, "id": target_id},
                )
                proposal = await _build_proposal(
                    db,
                    tc.function.name,
                    args,
                    identity=_require_write_identity(identity),
                    workspace_id=body.workspace_id or _workspace_id(body.context),
                    provenance=proposal_provenance,
                )
                tool_trace.append(
                    {
                        "name": tc.function.name,
                        "kind": "write",
                        "status": "proposed",
                        "argument_keys": sorted(args),
                    }
                )
                await _emit_activity(
                    "approval.required",
                    "等待确认",
                    proposal.summary,
                    state="attention",
                    target={"type": target_type, "id": target_id},
                )
                return ChatExecution(ChatReply(type="proposal", proposal=proposal), tool_trace)

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
            args = _safe_json(tc.function.arguments)
            label, target_type, target_id = _tool_public_description(tc.function.name, args)
            result = await _run_read_tool(
                db,
                tc.function.name,
                args,
                identity=identity,
                workspace_id=body.workspace_id or _workspace_id(body.context),
                proposal_provenance=proposal_provenance,
            )
            tool_trace.append(
                {
                    "name": tc.function.name,
                    "kind": "read",
                    "status": "completed",
                    "argument_keys": sorted(args),
                }
            )
            await _emit_activity(
                "tool.completed",
                label,
                _result_public_summary(result),
                state="completed",
                target={"type": target_type, "id": target_id},
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

    return ChatExecution(
        ChatReply(type="message", content="(达到工具调用步数上限, 请换个说法再试)"), tool_trace
    )


async def _chat_single_provider(
    db: AsyncSession,
    body: ChatRequest,
    identity: RequestIdentity | None,
    provider_id: str | None,
    *,
    tool_trace: list[dict[str, Any]] | None = None,
    proposal_provenance: ProposalProvenance | None = None,
) -> ApiResponse:
    """Run chat through one provider without failover.

    Use the requested provider when given; otherwise use the first enabled one.
    """
    provider = await _pick_provider(db, provider_id)
    client = await _build_client(provider)
    model = await _resolve_provider_model(db, provider, body.model_id)
    result = await _chat_with_client(
        client,
        model,
        body,
        db,
        identity,
        tool_trace=tool_trace,
        proposal_provenance=proposal_provenance,
    )
    return ApiResponse.ok(result.reply)


async def _resolve_provider_model(
    db: AsyncSession,
    provider: ModelProvider,
    requested_model_id: str | None,
) -> str:
    """Validate an explicit model against the selected provider's enabled catalog.

    The provider default remains a compatibility fallback for existing callers,
    including installations that have not synchronized a model catalog yet.
    """

    if requested_model_id is None:
        return provider.default_model or "gpt-4o-mini"
    model = await db.scalar(
        select(ProviderModel).where(
            ProviderModel.provider_id == provider.id,
            ProviderModel.model_id == requested_model_id,
            ProviderModel.model_type == "llm",
            ProviderModel.enabled.is_(True),
        )
    )
    if model is None:
        raise HTTPException(
            status_code=400,
            detail="指定的模型不属于所选 provider 或未启用",
        )
    return model.model_id


async def run_chat_request(
    db: AsyncSession,
    body: ChatRequest,
    identity: RequestIdentity | None,
    *,
    tool_trace: list[dict[str, Any]] | None = None,
    proposal_provenance: ProposalProvenance | None = None,
) -> ApiResponse:
    """Execute the existing chat provider/tool loop for persistent sessions."""
    if body.model_id and not body.provider_id:
        raise HTTPException(status_code=400, detail="显式模型必须绑定到 provider")
    if body.provider_id or not await resolver.has_candidates(db, "chat"):
        return await _chat_single_provider(
            db,
            body,
            identity,
            body.provider_id,
            tool_trace=tool_trace,
            proposal_provenance=proposal_provenance,
        )

    async def operation(adapter: Any, model_id: str) -> ApiResponse:
        provider = adapter.provider
        client = await _build_client(provider)
        model = model_id or provider.default_model or "gpt-4o-mini"
        result = await _chat_with_client(
            client,
            model,
            body,
            db,
            identity,
            tool_trace=tool_trace,
            proposal_provenance=proposal_provenance,
        )
        return ApiResponse.ok(result.reply)

    return await resolver.resolve_with_fallback(db, "chat", operation)


@router.get("/tools", response_model=ApiResponse[dict[str, Any]])
async def list_chat_tools(
    workspace_id: str,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
    *,
    project_id: str | None = None,
    workflow_id: str | None = None,
    run_id: str | None = None,
) -> ApiResponse:
    """Project the model tool registry through the caller's Workspace access."""

    from backend.services.agent_conversation_service import validate_context_binding
    from backend.services.studio_agent_session_access import resolve_agent_session_workspace

    context = {
        key: value
        for key, value in {
            "project_id": project_id,
            "workflow_id": workflow_id,
            "run_id": run_id,
        }.items()
        if value is not None
    }
    scope = await resolve_agent_session_workspace(
        db,
        identity,
        workspace_id,
        context=context,
    )
    await validate_context_binding(
        db,
        scope.workspace_id,
        context,
        studio_workspace_id=scope.studio_workspace_id,
    )
    access = scope.access
    tools: list[dict[str, Any]] = []
    for tool in TOOLS:
        function = tool["function"]
        name = function["name"]
        action = ACTION_REGISTRY.get(name) if name in WRITE_TOOLS else None
        permission = action.permission if action is not None else WorkspacePermission.READ
        available = access.allows(permission)
        item: dict[str, Any] = {
            "name": name,
            "label": _PUBLIC_TOOL_LABELS.get(name, (name, ""))[0],
            "description": function["description"],
            "kind": "proposal" if action is not None else "read",
            "requires_confirmation": action is not None,
            "available": available,
        }
        if not available:
            item["reason"] = f"workspace_permission_required:{permission.value}"
        tools.append(item)
    catalog: dict[str, Any] = {
        "version": "chat-tools/v1",
        "workspace_id": scope.workspace_id,
        "tools": tools,
    }
    if scope.studio_workspace_id is not None:
        catalog["studio_workspace_id"] = scope.studio_workspace_id
    return ApiResponse.ok(catalog)


@router.post("", response_model=ApiResponse[ChatReply])
async def chat(
    body: ChatRequest,
    identity: RequestIdentity | None = Depends(_optional_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse:
    """Agent dock chat, preserving the legacy unauthenticated read path."""
    await _emit_activity(
        "phase.changed",
        "理解目标",
        "正在结合当前页面、工作区和选中对象理解请求。",
        state="completed",
    )
    try:
        return await run_chat_request(db, body, identity)
    except (LlmAdapterError, ResolverError) as exc:
        # Business-level failure or every candidate skipped/failed. Keep the
        # existing HTTP boundary and sanitized provider error behavior.
        logger.error("chat failover | %s", exc)
        raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc


@router.post("/stream")
async def chat_stream(
    body: ChatRequest,
    identity: RequestIdentity | None = Depends(_optional_request_identity),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream ordered, durable execution facts as newline-delimited JSON."""
    run = await _create_durable_run(body, identity)

    async def event_source():
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

        async def produce() -> None:
            try:
                async with _RunScopedDurableEventWriter(run.id, queue) as writer:
                    writer.mark_running()
                    await writer.emit(
                        {
                            "type": "run.started",
                            "label": "开始处理",
                            "detail": "已接收请求，正在建立执行上下文。",
                            "state": "active",
                        }
                    )
                    await writer.flush()
                    token = _activity_sink.set(writer.emit)
                    try:
                        response = await chat(body, identity, db)
                        reply = response.data.model_dump(mode="json")
                        await writer.emit(
                            {
                                "type": "reply",
                                "label": "结果已就绪",
                                "detail": "本次处理已返回结果。",
                                "state": "completed",
                                "reply": reply,
                            }
                        )
                        await writer.finish(reply=reply)
                    except HTTPException as exc:
                        detail = str(exc.detail)
                        await writer.emit(
                            {
                                "type": "run.failed",
                                "label": "处理未完成",
                                "detail": detail,
                                "state": "failed",
                                "status": exc.status_code,
                                "recovery": "检查连接或目标状态后重试。",
                            }
                        )
                        await writer.finish(error=detail)
                    except Exception as exc:
                        logger.exception("chat stream failed")
                        detail = str(exc) or "Agent 暂时无法完成这项任务。"
                        await writer.emit(
                            {
                                "type": "run.failed",
                                "label": "处理未完成",
                                "detail": detail,
                                "state": "failed",
                                "status": 500,
                                "recovery": "稍后重试，或调整请求后继续。",
                            }
                        )
                        await writer.finish(error=detail)
                    finally:
                        _activity_sink.reset(token)
            except _DurableEventPersistenceError:
                await _persist_writer_failure(run.id, queue)
            except Exception:
                logger.exception("chat stream durable persistence failed")
                await _persist_writer_failure(run.id, queue)
            finally:
                await queue.put(None)

        task = asyncio.create_task(produce())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield json.dumps(event, ensure_ascii=False) + "\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        event_source(),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "X-Agent-Run-Id": run.id,
        },
    )


def _run_payload(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "session_id": run.session_id,
        "kind": run.kind,
        "status": run.status,
        "goal": run.goal,
        "reply": run.reply_payload,
        "error": run.error_message,
    }


async def _authorize_durable_run(
    db: AsyncSession,
    run: AgentRun,
    identity: RequestIdentity | None,
) -> None:
    session = await db.get(AgentSession, run.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Agent session not found")
    if session.actor_subject is not None and (
        identity is None or session.actor_subject != identity.subject
    ):
        raise HTTPException(status_code=403, detail="Agent run belongs to another identity")


@router.get("/runs/{run_id}", response_model=ApiResponse[dict[str, Any]])
async def get_chat_run(
    run_id: str,
    identity: RequestIdentity | None = Depends(_optional_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict[str, Any]]:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    await _authorize_durable_run(db, run, identity)
    return ApiResponse.ok(_run_payload(run))


@router.get("/runs/{run_id}/events", response_model=ApiResponse[list[dict[str, Any]]])
async def get_chat_run_events(
    run_id: str,
    identity: RequestIdentity | None = Depends(_optional_request_identity),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[list[dict[str, Any]]]:
    run = await db.get(AgentRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Agent run not found")
    await _authorize_durable_run(db, run, identity)
    result = await db.execute(
        select(AgentRunEvent)
        .where(AgentRunEvent.run_id == run_id)
        .order_by(AgentRunEvent.sequence.asc())
    )
    return ApiResponse.ok([event.payload for event in result.scalars().all()])


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


def _build_xml_tool_text() -> str:
    lines = ["\n\n你是采集网络操作 agent。可用工具:"]
    for tool in TOOLS:
        function = tool["function"]
        properties = function.get("parameters", {}).get("properties", {})
        required = set(function.get("parameters", {}).get("required", []))
        signature = ", ".join(name if name in required else f"{name}?" for name in properties)
        lines.append(f"- {function['name']}({signature}): {function['description']}")
    lines.extend(
        [
            "需要调用工具时, 严格输出 XML: "
            '<tool_use name="工具名" id="toolu_1">{json 参数}</tool_use>',
            "先用 list_* 拿到真实 id 再做写操作。不要用 markdown 代码块。",
        ]
    )
    return "\n".join(lines)


XML_TOOL_TEXT = _build_xml_tool_text()


async def _chat_xml(
    client: Any,
    model: str,
    system: str,
    body: ChatRequest,
    db: AsyncSession,
    identity: RequestIdentity | None,
    *,
    tool_trace: list[dict[str, Any]] | None = None,
    proposal_provenance: ProposalProvenance | None = None,
) -> ChatExecution:
    """Tool loop for XML-style models (parse <tool_use> from content, feed results back as text)."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system + XML_TOOL_TEXT}]
    messages += [{"role": m.role, "content": m.content} for m in body.messages]
    tool_trace = tool_trace if tool_trace is not None else []

    for _step in range(MAX_TOOL_STEPS):
        await db.commit()
        try:
            response = await client.chat.completions.create(
                model=model, messages=messages, max_tokens=XML_TOOL_MAX_TOKENS
            )
        except Exception as exc:
            logger.error("chat(xml) llm error | %s", exc)
            raise HTTPException(status_code=502, detail=f"模型调用失败: {exc}") from exc

        content = response.choices[0].message.content or ""
        calls = _parse_tool_use(content)

        if not calls:
            clean = _THINK_RE.sub("", content).strip()
            return ChatExecution(ChatReply(type="message", content=clean or "(无内容)"), tool_trace)

        # write tool hit → return proposal immediately
        for name, args in calls:
            if name in WRITE_TOOLS:
                if tool_trace is not None:
                    tool_trace.append(
                        {
                            "name": name,
                            "kind": "write",
                            "status": "proposal",
                            "argument_keys": sorted(args),
                        }
                    )
                proposal = await _build_proposal(
                    db,
                    name,
                    args,
                    identity=_require_write_identity(identity),
                    workspace_id=body.workspace_id or _workspace_id(body.context),
                    provenance=proposal_provenance,
                )
                tool_trace.append(
                    {
                        "name": name,
                        "kind": "write",
                        "status": "proposed",
                        "argument_keys": sorted(args),
                    }
                )
                return ChatExecution(ChatReply(type="proposal", proposal=proposal), tool_trace)

        # read tools → execute, feed results back as <tool_result> text, loop
        messages.append({"role": "assistant", "content": content})
        for name, args in calls:
            if tool_trace is not None:
                tool_trace.append(
                    {
                        "name": name,
                        "kind": "read",
                        "status": "completed",
                        "argument_keys": sorted(args),
                    }
                )
            result = await _run_read_tool(
                db,
                name,
                args,
                identity=identity,
                workspace_id=body.workspace_id or _workspace_id(body.context),
                proposal_provenance=proposal_provenance,
            )
            tool_trace.append(
                {"name": name, "kind": "read", "status": "completed", "argument_keys": sorted(args)}
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f'<tool_result name="{name}">'
                        f"{json.dumps(result, ensure_ascii=False)}</tool_result>"
                    ),
                }
            )

    return ChatExecution(
        ChatReply(type="message", content="(达到工具调用步数上限, 请换个说法再试)"), tool_trace
    )
