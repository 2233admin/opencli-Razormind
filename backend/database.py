import asyncio
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable
from inspect import isawaitable

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from backend.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

AfterCommitCallback = Callable[[], Awaitable[None] | None]
_AFTER_COMMIT_CALLBACKS_KEY = "opencli_after_commit_callbacks"

# SQLite: NullPool avoids multi-connection lock contention (each session gets its own
# fresh connection, no pooling). PostgreSQL uses default QueuePool.
_pool_kwargs = {"poolclass": NullPool} if settings.is_sqlite else {}
# timeout=30: sqlite3 native busy-wait (seconds); check_same_thread=False: allow async
connect_args = {"check_same_thread": False, "timeout": 30} if settings.is_sqlite else {}

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=connect_args,
    **_pool_kwargs,
)

if settings.is_sqlite:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


def queue_after_commit(session: AsyncSession, callback: AfterCommitCallback) -> None:
    """Queue non-authoritative publication until this session commits."""

    callbacks = session.info.setdefault(_AFTER_COMMIT_CALLBACKS_KEY, [])
    callbacks.append(callback)


def clear_after_commit_callbacks(session: AsyncSession) -> None:
    session.info.pop(_AFTER_COMMIT_CALLBACKS_KEY, None)


async def rollback_session(session: AsyncSession) -> None:
    clear_after_commit_callbacks(session)
    await session.rollback()


async def rollback_session_preserving_primary(
    session: AsyncSession,
    primary_error: Exception,
) -> None:
    """Best-effort rollback that never replaces an active primary exception."""

    try:
        await rollback_session(session)
    except Exception:
        logger.exception(
            "Database rollback cleanup failed; preserving primary exception",
            extra={"primaryExceptionType": type(primary_error).__name__},
        )


async def commit_session(session: AsyncSession) -> None:
    """Commit authoritative state, then run queued best-effort publications."""

    try:
        await session.commit()
    except Exception as exc:
        await rollback_session_preserving_primary(session, exc)
        raise

    callbacks = session.info.pop(_AFTER_COMMIT_CALLBACKS_KEY, [])
    for callback in callbacks:
        try:
            result = callback()
            if isawaitable(result):
                await result
        except Exception:
            logger.exception("After-commit callback failed; authoritative transaction is committed")


async def get_db() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await commit_session(session)
        except Exception as exc:
            await rollback_session_preserving_primary(session, exc)
            raise
        finally:
            clear_after_commit_callbacks(session)
            await session.close()


async def run_migrations() -> None:
    """Run Alembic migrations to head.

    If the database has no alembic_version table yet (i.e., it was created by
    the old create_all path), stamp it at the initial revision before upgrading
    so Alembic doesn't try to recreate existing tables.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    logger = logging.getLogger(__name__)

    alembic_cfg = Config("alembic.ini")

    # Check if existing DB was created without Alembic (no alembic_version table)
    async with engine.connect() as conn:
        def _needs_stamp(sync_conn) -> bool:
            insp = inspect(sync_conn)
            tables = insp.get_table_names()
            return "data_sources" in tables and "alembic_version" not in tables

        needs_stamp = await conn.run_sync(_needs_stamp)

    if needs_stamp:
        logger.info(
            "Existing database detected without alembic_version; "
            "stamping at initial revision before upgrading"
        )
        def _stamp():
            command.stamp(alembic_cfg, "5a9a94795d00")
        await asyncio.to_thread(_stamp)

    def _upgrade():
        command.upgrade(alembic_cfg, "head")

    await asyncio.to_thread(_upgrade)
    logger.info("Database migrations applied")
