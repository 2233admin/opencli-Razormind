"""Pipeline orchestrator: collect → normalize → store → [ai] → [notify]."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import select

from backend.channels.base import ChannelFetchError
from backend.control.error_kinds import map_error_type, map_exception
from backend.control.recorder import FreshnessInfo, record_run_measurement
from backend.models.source import DataSource
from backend.pipeline import events
from backend.pipeline.error_taxonomy import effective_error_type, is_captcha, is_retryable

logger = logging.getLogger(__name__)


def _parse_item_timestamp(value: Any) -> datetime | None:
    """Best-effort parse of a normalized ``published_at`` string into an aware
    datetime. Handles RFC 822 (what feedparser/RSS produces) and ISO-8601.
    Returns None (never raises) for anything that doesn't parse — an
    unparseable string is evidence of ``source_ts_quality="invalid"``, not a
    crash.
    """
    if not value or not isinstance(value, str):
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def _derive_freshness(new_records: list[Any], now: datetime) -> FreshnessInfo:
    """Honestly derive freshness quality from whatever item timestamps the
    sink's normalized records already carry (``normalized_data['published_at']``
    — set by ``backend.pipeline.normalizer`` from the item's own date field,
    e.g. RSS's ``published``). No channel wiring is added here — if a channel
    never produced a date field, or none of it parses, this returns
    ``quality="missing"``/``"invalid"`` rather than fabricating a timestamp.

    * Every accepted record's raw published_at string is empty → "observed_fallback"
      (there's no source-provided time signal at all; wall-clock collection time
      stands in for it).
    * At least one non-empty published_at string, all unparsable → "invalid".
    * At least one parses → "source", using the newest parsed value.
    * No accepted records at all this run → "missing" (nothing to derive from).
    """
    if not new_records:
        return FreshnessInfo(newest_observed_at=now, quality="missing")

    raw_values: list[str] = []
    for rec in new_records:
        normalized = getattr(rec, "normalized_data", None) or {}
        raw_values.append(normalized.get("published_at") or "")

    if not any(raw_values):
        return FreshnessInfo(newest_observed_at=now, quality="observed_fallback")

    parsed = [p for v in raw_values if v and (p := _parse_item_timestamp(v)) is not None]
    if not parsed:
        return FreshnessInfo(newest_observed_at=now, quality="invalid")

    newest_source_ts = max(parsed)
    lag = int((now - newest_source_ts).total_seconds())
    return FreshnessInfo(
        newest_source_ts=newest_source_ts,
        newest_observed_at=now,
        freshness_lag_seconds=lag,
        quality="source",
    )


async def _record_measurement_best_effort(**kwargs: Any) -> None:
    """Wrap ``record_run_measurement`` in its own short-lived session, commit,
    and swallow failures (mirrors ``events.emit``): a measurement-recording bug
    must never fail or mask the run it's trying to observe (§0 — the sensor
    must not become a new source of run failures either).
    """
    try:
        from backend.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await record_run_measurement(session, **kwargs)
            await session.commit()
    except Exception as exc:
        logger.warning("record_run_measurement failed (non-fatal): %s", exc)


@dataclass
class PipelineResult:
    success: bool
    source_id: str
    collected: int = 0
    stored: int = 0
    skipped: int = 0
    ai_processed: int = 0
    notifications_sent: int = 0
    error: str | None = None
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


async def run_pipeline(
    task_id: str,
    source: DataSource,
    parameters: dict[str, Any] | None = None,
    enable_ai: bool = True,
    enable_notifications: bool = True,
    agent_config: dict[str, Any] | None = None,
    run_id: str | None = None,
    sink=None,  # ItemSink | None — write destination; defaults to LegacyDbSink
) -> PipelineResult:
    """Execute the full collection pipeline. Each write step uses its own
    short-lived session so no write lock is held during long-running I/O."""
    from backend.database import AsyncSessionLocal
    from backend.pipeline import ai_processor, collector, notifier_dispatch

    started = datetime.now(timezone.utc)
    params = parameters or {}

    # Pre-step: auto-resolve chrome endpoint from a browser binding. Channels that
    # declare capabilities.session_affinity (opencli, skill) drive a real Chrome
    # from the shared pool, so a site-keyed binding lets them attach to a
    # logged-in browser. Best-effort: a missing binding is not an error
    # (browser_pool.acquire(endpoint=None) picks a default), so we only override
    # chrome_endpoint when a binding exists. Gated by the capability rather than a
    # hardcoded channel list, so a new session-bound channel needs no change here.
    from backend.channels.registry import get_channel

    try:
        _affinity_channel = get_channel(source.channel_type)
    except Exception:
        _affinity_channel = None  # unknown channel_type surfaces in the collect step
    if (
        _affinity_channel is not None
        and _affinity_channel.capabilities.session_affinity
        and not params.get("chrome_endpoint")
    ):
        site = source.channel_config.get("site", "")
        if site:
            from backend.services import browser_service
            async with AsyncSessionLocal() as session:
                binding = await browser_service.get_binding_by_site(session, site)
                if binding:
                    params = {**params, "chrome_endpoint": binding.browser_endpoint}
                    logger.info("[task:%s] auto-binding | site=%s → %s",
                                task_id, site, binding.browser_endpoint)

    # Step 1: Collect
    logger.info("[task:%s] step1/collect start | source=%s channel=%s params=%s",
                task_id, source.name, source.channel_type, params)
    step1_start = datetime.now(timezone.utc)

    if run_id:
        # Skill channel: inject run_id into params BEFORE dispatch so the loop can
        # emit per-step events via events.emit(run_id, ...). Scoped to "skill" —
        # other channels don't expect a run_id param. (chrome_endpoint, if any,
        # was already injected by the pre-step binding above.)
        if source.channel_type == "skill":
            params = {**params, "run_id": run_id}
        collect_detail: dict = {"channel_type": source.channel_type, "params": params}
        if source.channel_type == "skill":
            _skill_md = source.channel_config.get("skill_md") or ""
            collect_detail["skill"] = {
                "skill_chars": len(_skill_md),
                "has_chrome_endpoint": bool(params.get("chrome_endpoint")),
                "auto_confirm": bool(source.channel_config.get("auto_confirm", False)),
            }
        if source.channel_type == "opencli":
            from backend.channels.opencli_channel import _OPENCLI_BIN, _peek_named_options
            cfg = source.channel_config
            _site = cfg.get("site", "")
            _cmd = cfg.get("command", "")
            _raw_args = {**cfg.get("args", {}), **{k: v for k, v in params.items() if k != "chrome_endpoint"}}
            _pos = [str(v) for v in cfg.get("positional_args", [])]
            _fmt = cfg.get("format", "json")
            # Apply same positional-resolution logic as the channel, but this
            # detail string is display-only (the channel's own collect() call
            # re-derives named options for real dispatch) — so peek the cache
            # instead of spawning an opencli --help subprocess on the
            # collection hot path just to format a log line (C17). A cache
            # miss here behaves exactly like a failed --help fetch always
            # did: fall through and treat every raw arg as a named option.
            _named_opts = _peek_named_options(_OPENCLI_BIN, _site, _cmd) or frozenset()
            _named_args, _extra_pos = {}, []
            for k, v in _raw_args.items():
                if _named_opts and k not in _named_opts:
                    _extra_pos.append(str(v))
                else:
                    _named_args[k] = v
            _all_pos = _extra_pos + _pos
            _parts = ["opencli", _site, _cmd] + _all_pos
            for k, v in _named_args.items():
                _parts += [f"--{k}", str(v)]
            _parts += ["-f", _fmt]
            collect_detail["command"] = " ".join(_parts)
        await events.emit(
            run_id, "collect",
            f"开始采集 | 渠道={source.channel_type} 数据源={source.name}",
            detail=collect_detail,
        )

    try:
        channel_result = await collector.collect(source, params)
    except Exception as exc:
        error_type = effective_error_type(exc)
        logger.exception(
            "[task:%s] step1/collect exception | error_type=%s | %s", task_id, error_type, exc
        )
        if run_id:
            await events.emit(
                run_id, "collect",
                f"采集失败: {exc}",
                level="error",
                detail={"error": str(exc), "error_type": error_type},
            )
        if is_retryable(error_type):
            # Let this propagate to the celery task boundary so its
            # autoretry_for policy applies instead of burning a permanent
            # failure on a transient fault.
            raise
        if run_id:
            await _record_measurement_best_effort(
                source_id=source.id, run_id=run_id,
                fetch_latency_ms=int((datetime.now(timezone.utc) - step1_start).total_seconds() * 1000),
                error_kind=map_exception(exc),
                raw={"stage": "collect", "error": str(exc), "error_type": error_type},
            )
        return PipelineResult(success=False, source_id=source.id, error=str(exc))

    if not channel_result.success:
        logger.error(
            "[task:%s] step1/collect failed | error=%s error_type=%s",
            task_id, channel_result.error, channel_result.error_type,
        )
        if run_id:
            await events.emit(
                run_id, "collect",
                f"采集失败: {channel_result.error}",
                level="error",
                detail={"error": channel_result.error, "error_type": channel_result.error_type},
            )
        if is_retryable(channel_result.error_type):
            raise ChannelFetchError(channel_result.error or "collect failed")
        if is_captcha(channel_result.error_type):
            # Human-cleared challenge wall (Doubao captcha). Automatic retry
            # would burn budget on the same wall and a permanent failure hides
            # the recovery path, so instead pause the source (scheduler
            # already skips disabled sources) and flag it for review — a human
            # clears the wall, TTL expiry auto-resumes. Best-effort: a DB or
            # actuator failure here must not mask the original collect error.
            try:
                from backend.config import get_settings
                from backend.control.actuator import pause_source_for_captcha
                from backend.database import AsyncSessionLocal

                ttl = get_settings().control_pause_ttl_seconds
                async with AsyncSessionLocal() as session:
                    src = await session.get(DataSource, source.id)
                    if src is not None:
                        await pause_source_for_captcha(
                            session,
                            source=src,
                            now=datetime.now(timezone.utc),
                            ttl_seconds=ttl,
                        )
                        await session.commit()
                        logger.warning(
                            "[task:%s] captcha wall | paused source=%s (ttl=%ss, review_required)",
                            task_id, source.id, ttl,
                        )
                        if run_id:
                            await events.emit(
                                run_id, "collect",
                                "验证码拦截：数据源已暂停，等待人工处理",
                                level="warning",
                                detail={"captcha_paused": True, "pause_ttl_seconds": ttl},
                            )
            except Exception:
                logger.exception("[task:%s] failed to pause source on captcha", task_id)
            return PipelineResult(
                success=False,
                source_id=source.id,
                error=channel_result.error,
                metadata={"captcha_paused": True},
            )
        if run_id:
            await _record_measurement_best_effort(
                source_id=source.id, run_id=run_id,
                fetch_latency_ms=int((datetime.now(timezone.utc) - step1_start).total_seconds() * 1000),
                error_type=channel_result.error_type,
                raw={"stage": "collect", "error": channel_result.error},
            )
        return PipelineResult(success=False, source_id=source.id, error=channel_result.error)

    step1_elapsed = int((datetime.now(timezone.utc) - step1_start).total_seconds() * 1000)
    logger.info("[task:%s] step1/collect done | count=%d metadata=%s",
                task_id, channel_result.count, channel_result.metadata)
    if run_id:
        chrome_mode = channel_result.metadata.get("chrome_mode")
        mode_label = f" | Chrome={chrome_mode}" if chrome_mode else ""
        await events.emit(
            run_id, "collect",
            f"采集完成 | 获取 {channel_result.count} 条{mode_label}",
            detail={"count": channel_result.count, "metadata": channel_result.metadata},
            elapsed_ms=step1_elapsed,
        )

    # Steps 2+3: Normalize + Store, behind the write seam. The sink owns its own
    # normalization, dedup, and persistence; the orchestrator stays
    # destination-agnostic. An explicitly injected sink wins (tests, callers);
    # otherwise the source's write_strategy selects it (default 'legacy' →
    # LegacyDbSink, the original inline path).
    from backend.pipeline.sinks.base import RunContext
    from backend.pipeline.sinks.strategy import select_sink

    active_sink = sink or select_sink(getattr(source, "write_strategy", None))
    sink_ctx = RunContext(
        task_id=task_id,
        source_id=source.id,
        provider=source.channel_type,
        run_id=run_id,
    )
    logger.info("[task:%s] step2-3/sink start | sink=%s items=%d",
                task_id, type(active_sink).__name__, channel_result.count)
    try:
        sink_result = await active_sink.write_batch(sink_ctx, channel_result.items)
    except Exception as exc:
        error_type = effective_error_type(exc)
        logger.exception(
            "[task:%s] step2-3/sink exception | error_type=%s | %s", task_id, error_type, exc
        )
        if run_id:
            await events.emit(
                run_id, "store",
                f"持久化失败: {exc}",
                level="error",
                detail={"error": str(exc), "error_type": error_type},
            )
        if is_retryable(error_type):
            raise
        if run_id:
            await _record_measurement_best_effort(
                source_id=source.id, run_id=run_id,
                accepted=0, duplicates=0, rejected=channel_result.count,
                fetch_latency_ms=step1_elapsed,
                error_kind=map_exception(exc),
                raw={"stage": "store", "error": str(exc), "error_type": error_type},
            )
        return PipelineResult(
            success=False,
            source_id=source.id,
            collected=channel_result.count,
            error=str(exc),
        )
    new_records = sink_result.records
    skipped = sink_result.duplicates
    logger.info("[task:%s] step2-3/sink done | normalized=%d new=%d skipped=%d",
                task_id, sink_result.normalized, len(new_records), skipped)
    if run_id:
        await events.emit(
            run_id, "normalize",
            f"归一化完成 | {sink_result.normalized} 条",
            detail={"items": sink_result.normalized},
        )
        await events.emit(
            run_id, "store",
            f"入库完成 | 新增 {len(new_records)} 条，跳过 {skipped} 条（重复）",
            detail={"new": len(new_records), "skipped": skipped},
        )

    # Shadow-sink errors (e.g. DualSink's best-effort ODP forward failing while
    # the legacy write still succeeded) are non-blocking by design — the run
    # must still complete — but must not vanish silently either. Surface them
    # as a warning event and onto PipelineResult.metadata so a source with ODP
    # down consistently shows a signal, not just a worker-log line (P1-7).
    shadow_errors = list(sink_result.errors)
    if shadow_errors:
        logger.warning(
            "[task:%s] step2-3/sink shadow errors | count=%d errors=%s",
            task_id, len(shadow_errors), shadow_errors,
        )
        if run_id:
            await events.emit(
                run_id, "store",
                f"影子写入出现 {len(shadow_errors)} 个错误（不影响本次任务结果）",
                level="warning",
                detail={
                    "shadow_errors": shadow_errors,
                    "shadow_meta": sink_result.shadow_meta,
                },
            )

    # Incremental cursor: advance the persisted cursor ONLY now that the write sink
    # has accepted this batch. A raised/failed sink returned above, so reaching here
    # means the data landed; committing during fetch would skip items that never got
    # written. (Deeper ODP durability — a queued 202 that never persists — is an
    # ODP-side guarantee, tracked separately.)
    pending_cursor = channel_result.metadata.pop("__cursor_pending__", None)
    cursor_source_id = channel_result.metadata.pop("__cursor_source_id__", None)
    # Real commit result (backend.pipeline.cursor_store.CommitResult), not a
    # guess — False for non-incremental channels/runs that never staged a
    # cursor at all, so cursor_advanced in the measurement below reflects
    # what actually got persisted, never "save() was called".
    cursor_advanced = False
    if pending_cursor is not None and cursor_source_id is not None:
        from backend.pipeline.cursor_store import DBCursorStore

        # AUDIT C11: this used to be the only post-sink-write step without
        # error handling — the sink already durably committed this batch's
        # records above, so a cursor-save failure here must not fail the run
        # (that would false-fail a run whose data landed, and Celery would
        # re-collect the same window on retry, re-storing already-stored
        # records). Log loud enough to find the stuck cursor, surface a
        # warning event when there's a run to attach it to, and keep going
        # with cursor_advanced=False — the measurement below then honestly
        # reflects "didn't advance" rather than guessing.
        try:
            commit_result = await DBCursorStore().save(cursor_source_id, pending_cursor)
        except Exception as exc:
            logger.error(
                "[task:%s] cursor save failed (non-fatal, run stays successful) | "
                "source=%s cursor=%r | %s",
                task_id, cursor_source_id, pending_cursor, exc,
            )
            if run_id:
                await events.emit(
                    run_id, "store",
                    f"游标保存失败（不影响本次任务结果）: {exc}",
                    level="warning",
                    detail={
                        "source_id": cursor_source_id,
                        "cursor": pending_cursor,
                        "error": str(exc),
                    },
                )
        else:
            cursor_advanced = commit_result.advanced
            logger.info("[task:%s] cursor committed post-write | source=%s advanced=%s",
                        task_id, cursor_source_id, cursor_advanced)

    # Step 4: AI processing
    effective_ai_config = agent_config or source.ai_config
    ai_count = 0
    if enable_ai and effective_ai_config and new_records:
        processor_type = effective_ai_config.get("processor_type", "claude")
        model = effective_ai_config.get("model", "")
        logger.info("[task:%s] step4/ai start | processor=%s model=%s records=%d",
                    task_id, processor_type, model, len(new_records))

        async with AsyncSessionLocal() as session:
            from backend.models.task import CollectionTask
            task_row = await session.get(CollectionTask, task_id)
            if task_row:
                task_row.status = "ai_processing"
                await session.commit()
        try:
            ai_count = await ai_processor.process_with_ai(
                new_records,
                effective_ai_config,
                source_id=source.id,
                # model-provider runtime PR-F decision #9 dual-track resolution only applies to
                # DataSource.ai_config; an agent_config override already went
                # through its own (untouched) ai_agents.provider_id resolution
                # in backend.pipeline.runner phase 2, so it's used as-is.
                resolve_provider=agent_config is None,
            )
            # Persist enrichments — new_records are detached after step3 session
            # closed. AUDIT C21: one bulk SELECT ... WHERE id IN (...) + an
            # in-memory id->row map, instead of one `session.get` per record
            # (N+1) — same field writes (ai_enrichment, status="ai_processed").
            from backend.models.record import CollectedRecord
            enriched_ids = [rec.id for rec in new_records if rec.ai_enrichment is not None]
            if enriched_ids:
                async with AsyncSessionLocal() as session:
                    db_recs = (
                        await session.execute(
                            select(CollectedRecord).where(CollectedRecord.id.in_(enriched_ids))
                        )
                    ).scalars().all()
                    db_recs_by_id = {db_rec.id: db_rec for db_rec in db_recs}
                    for rec in new_records:
                        if rec.ai_enrichment is None:
                            continue
                        db_rec = db_recs_by_id.get(rec.id)
                        if db_rec:
                            db_rec.ai_enrichment = rec.ai_enrichment
                            db_rec.status = "ai_processed"
                    await session.commit()
            logger.info("[task:%s] step4/ai done | processed=%d", task_id, ai_count)
            if run_id:
                # AUDIT C3: ai_count is now the real enrichment count (0 when
                # processor_type is unknown/misconfigured) — a config error
                # must read as "failed/skipped", never "完成".
                if ai_count > 0:
                    await events.emit(
                        run_id, "ai_process",
                        f"AI 处理完成 | {ai_count} 条",
                        detail={"processed": ai_count},
                    )
                else:
                    await events.emit(
                        run_id, "ai_process",
                        f"AI 处理失败/跳过 | processor_type={processor_type!r} 未产生任何富化记录",
                        level="warning",
                        detail={"processed": 0, "processor_type": processor_type},
                    )
        except Exception as exc:
            logger.warning("[task:%s] step4/ai failed | %s", task_id, exc)
            if run_id:
                await events.emit(
                    run_id, "ai_process",
                    f"AI 处理失败: {exc}",
                    level="warning",
                )
    elif enable_ai and not effective_ai_config:
        logger.debug("[task:%s] step4/ai skipped | no ai_config", task_id)
        if run_id:
            await events.emit(run_id, "ai_process", "跳过 AI 处理（未配置）")

    # Step 5: Notify
    notifications_sent = 0
    if enable_notifications and new_records:
        logger.info("[task:%s] step5/notify start | records=%d", task_id, len(new_records))
        try:
            # dispatch_notifications manages its own write-lock-free phasing
            # internally (AUDIT C1/C23): it commits phase A's pending rows on
            # this session, performs the sends with no session open, then
            # persists outcomes via its own short-lived session — so no
            # explicit commit is needed here.
            async with AsyncSessionLocal() as session:
                notify_summary = await notifier_dispatch.dispatch_notifications(
                    session, source.id, new_records
                )
            notifications_sent = notify_summary.get("sent", 0)
            notifications_failed = notify_summary.get("failed", 0)
            # AUDIT C12: report the real aggregate, not an unconditional
            # "done" — and if every attempted send failed, that's a warning,
            # not routine info.
            all_failed = notifications_failed > 0 and notifications_sent == 0
            log_fn = logger.warning if all_failed else logger.info
            log_fn(
                "[task:%s] step5/notify done | sent=%d failed=%d",
                task_id, notifications_sent, notifications_failed,
            )
            if run_id:
                await events.emit(
                    run_id, "notify",
                    f"通知发送完成 | 成功 {notifications_sent} 失败 {notifications_failed}",
                    level="warning" if all_failed else "info",
                    detail={"sent": notifications_sent, "failed": notifications_failed},
                )
        except Exception as exc:
            logger.warning("[task:%s] step5/notify failed | %s", task_id, exc)
            if run_id:
                await events.emit(
                    run_id, "notify",
                    f"通知发送失败: {exc}",
                    level="warning",
                )

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)

    if shadow_errors:
        # Non-blocking signal onto the result too (in addition to the emitted
        # event above), so a caller with no run_id (or one that persists
        # PipelineResult.metadata onto TaskRun, see runner.py) still observes
        # the shadow failure instead of it only living in a worker log line.
        channel_result.metadata["shadow_errors"] = shadow_errors
        if sink_result.shadow_meta is not None:
            channel_result.metadata["shadow_meta"] = sink_result.shadow_meta

    if run_id:
        await events.emit(
            run_id, "complete",
            f"任务完成 | 总耗时 {duration_ms}ms | 采集 {channel_result.count} 新增 {len(new_records)} 跳过 {skipped}",
            detail={
                "duration_ms": duration_ms,
                "collected": channel_result.count,
                "stored": len(new_records),
                "skipped": skipped,
            },
        )
        completed_at = datetime.now(timezone.utc)
        # A successful run has no terminal error_type by definition — shadow-sink
        # errors are non-blocking (the run still succeeded) and are already
        # surfaced via the "complete" event above and PipelineResult.metadata;
        # they're carried into raw here too so they're visible alongside the
        # measurement, without fabricating a fake error_kind for a run that
        # actually succeeded.
        await _record_measurement_best_effort(
            source_id=source.id, run_id=run_id,
            accepted=len(new_records), duplicates=skipped, rejected=sink_result.rejected,
            fetch_latency_ms=step1_elapsed, store_latency_ms=duration_ms - step1_elapsed,
            cursor_advanced=cursor_advanced,
            freshness=_derive_freshness(new_records, completed_at),
            raw={
                "stage": "complete",
                "collected": channel_result.count,
                "duration_ms": duration_ms,
                "shadow_errors": shadow_errors or None,
            },
            measured_at=completed_at,
        )

    return PipelineResult(
        success=True,
        source_id=source.id,
        collected=channel_result.count,
        stored=len(new_records),
        skipped=skipped,
        ai_processed=ai_count,
        notifications_sent=notifications_sent,
        duration_ms=duration_ms,
        metadata=channel_result.metadata,
    )
