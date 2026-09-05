"""Platform-admin account registry for the existing platform-wide browser fleet."""

import asyncio
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.v1.browser_spaces import BrowserSpaceRoute
from backend.browser_pool import NoReadyBrowserSlotError, get_pool
from backend.database import get_db
from backend.models.browser import BrowserInstance
from backend.models.browser_account import BrowserAccount
from backend.schemas.browser_account import (
    BrowserAccountConfirmation,
    BrowserAccountCreate,
    BrowserAccountDelete,
    BrowserAccountUpdate,
    BrowserLoginAction,
)
from backend.schemas.common import ApiResponse
from backend.security.identity import RequestIdentity, get_request_identity, is_platform_admin
from backend.services import browser_login_display
from backend.services.browser_account_service import (
    PLATFORMS,
    account_or_404,
    account_read,
    require_unassigned,
    resolve_website,
)


async def require_admin(identity: RequestIdentity = Depends(get_request_identity)):
    if not is_platform_admin(identity):
        raise HTTPException(403, "仅平台管理员可以管理浏览器登录账号")
    return identity


router = APIRouter(
    prefix="/browser-accounts",
    tags=["browser-accounts"],
    dependencies=[Depends(require_admin)],
    route_class=BrowserSpaceRoute,
)


async def account_operation(account_id: str, db: AsyncSession = Depends(get_db)):
    """Atomic cross-worker lease serializes display, input and destructive work."""
    token = str(uuid4())
    now = datetime.now(UTC)
    result = await db.execute(
        update(BrowserAccount)
        .where(
            BrowserAccount.id == account_id,
            or_(BrowserAccount.operation_until.is_(None), BrowserAccount.operation_until < now),
        )
        .values(operation_token=token, operation_until=now + timedelta(minutes=5))
        .execution_options(synchronize_session=False)
    )
    await db.commit()
    if result.rowcount != 1:
        await account_or_404(db, account_id)
        raise HTTPException(409, "此账号的登录操作正在进行，请稍后重试")
    db.expire_all()
    try:
        yield
    finally:
        await db.rollback()
        await db.execute(
            update(BrowserAccount)
            .where(
                BrowserAccount.id == account_id,
                BrowserAccount.operation_token == token,
            )
            .values(operation_token=None, operation_until=None)
            .execution_options(synchronize_session=False)
        )
        await db.commit()


@router.get("/websites")
async def website_shortcuts():
    return ApiResponse.ok(
        [{"label": item["label"], "url": item["url"]} for item in PLATFORMS.values()]
    )


@router.get("")
async def list_accounts(db: AsyncSession = Depends(get_db)):
    accounts = (await db.scalars(select(BrowserAccount).order_by(BrowserAccount.created_at))).all()
    instances = {item.id: item for item in (await db.scalars(select(BrowserInstance))).all()}
    assigned = {account.browser_instance_id for account in accounts}
    pool = get_pool()
    return ApiResponse.ok(
        {
            "accounts": [
                account_read(item, instances.get(item.browser_instance_id), pool)
                for item in accounts
                if item.status != "archived"
            ],
            "archived_accounts": [
                account_read(item, instances.get(item.browser_instance_id), pool)
                for item in accounts
                if item.status == "archived"
            ],
            "available_instances": [
                {
                    "id": item.id,
                    "label": item.label or item.profile_name,
                    "profile_name": item.profile_name,
                }
                for item in instances.values()
                if item.id not in assigned
                and item.profile_kind == "authenticated"
                and item.endpoint in pool.endpoints
                and not item.login_reserved
            ],
        }
    )


@router.post("", status_code=201)
async def create_account(body: BrowserAccountCreate, db: AsyncSession = Depends(get_db)):
    from backend.browser_pool import LocalBrowserPool

    if not isinstance(get_pool(), LocalBrowserPool):
        raise HTTPException(409, "当前分布式浏览器池尚不支持自动账号登录空间，请使用本地浏览器池")
    site_url, domain = await resolve_website(body.site_url, body.platform)
    website_name = next(
        (
            item["label"]
            for item in PLATFORMS.values()
            if domain == urlparse(item["url"]).hostname.removeprefix("www.")
        ),
        domain,
    )
    label = body.label or f"{website_name}账号 {uuid4().hex[:4]}"
    if body.browser_instance_id:
        instance = await db.get(BrowserInstance, body.browser_instance_id)
        if instance is None:
            raise HTTPException(404, "浏览器实例不存在")
    else:
        import json

        from backend.api.v1.browser_containers import add_chrome_instance

        profile = f"account-{uuid4().hex}"
        for attempt in range(3):
            try:
                await add_chrome_instance(
                    count=1,
                    profile_name=profile,
                    resource_class="medium",
                    startup_pages=json.dumps([site_url]),
                    db=db,
                )
                break
            except IntegrityError:
                # A concurrent creator claimed this endpoint first. Its committed
                # row will be included when the allocator computes the next index.
                await db.rollback()
                if attempt == 2:
                    raise HTTPException(409, "同时添加的账号较多，请稍后重试") from None
        instance = await db.scalar(
            select(BrowserInstance).where(BrowserInstance.profile_name == profile)
        )
        if instance is None:
            raise HTTPException(503, "浏览器创建未完成，请刷新后选择已有实例重试")
    if instance.profile_kind != "authenticated":
        raise HTTPException(409, "匿名浏览器不能保存账号登录，请选择持久化登录实例")
    pool = get_pool()
    if instance.endpoint not in pool.endpoints:
        raise HTTPException(409, "浏览器不在当前池中，请先恢复浏览器实例")
    await require_unassigned(db, instance.id)
    try:
        async with asyncio.timeout(5):
            async with pool.acquire(instance.endpoint) as acquired_endpoint:
                if acquired_endpoint != instance.endpoint:
                    raise HTTPException(409, "浏览器实例已变化，请刷新后重试")
                await db.refresh(instance)
                await require_unassigned(db, instance.id)
                account = BrowserAccount(
                    platform=domain,
                    site_url=site_url,
                    label=label,
                    browser_instance_id=instance.id,
                    profile_name=instance.profile_name,
                )
                instance.login_reserved = True
                db.add(account)
                await db.commit()
    except (TimeoutError, NoReadyBrowserSlotError) as exc:
        await db.rollback()
        raise HTTPException(409, "浏览器正忙或已被保留，请稍后重试") from exc
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(409, "该浏览器已绑定另一个账号，请刷新后重试") from exc
    await db.refresh(account)
    return ApiResponse.ok(account_read(account, instance, get_pool()))


async def _login_frame(account_id: str, action: dict, response: Response, db: AsyncSession):
    account = await account_or_404(db, account_id)
    instance = await db.get(BrowserInstance, account.browser_instance_id)
    data = await browser_login_display.display(account, instance, action)
    target = data.pop("target_id")
    if account.login_target_id != target:
        account.login_target_id = target
        await db.commit()
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"
    return ApiResponse.ok(data)


@router.post("/{account_id}/login", dependencies=[Depends(account_operation, scope="function")])
async def open_login(account_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    return await _login_frame(account_id, {"kind": "open"}, response, db)


@router.get("/{account_id}/frame", dependencies=[Depends(account_operation, scope="function")])
async def login_frame(account_id: str, response: Response, db: AsyncSession = Depends(get_db)):
    return await _login_frame(account_id, {"kind": "frame"}, response, db)


@router.post("/{account_id}/input", dependencies=[Depends(account_operation, scope="function")])
async def login_input(
    account_id: str,
    body: BrowserLoginAction,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    return await _login_frame(account_id, body.model_dump(), response, db)


@router.patch("/{account_id}", dependencies=[Depends(account_operation, scope="function")])
async def rename_account(
    account_id: str, body: BrowserAccountUpdate, db: AsyncSession = Depends(get_db)
):
    account = await account_or_404(db, account_id)
    account.label = body.label
    await db.commit()
    return ApiResponse.ok(None)


@router.post(
    "/{account_id}/confirmation", dependencies=[Depends(account_operation, scope="function")]
)
async def confirm_account(
    account_id: str,
    body: BrowserAccountConfirmation,
    identity: RequestIdentity = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    account = await account_or_404(db, account_id)
    instance = await db.get(BrowserInstance, account.browser_instance_id)
    if account.status in {"archived", "deleting"}:
        raise HTTPException(409, "账号已归档或正在删除")
    if (
        not instance
        or instance.profile_name != account.profile_name
        or instance.profile_kind != "authenticated"
    ):
        raise HTTPException(409, "浏览器 Profile 已改变，请先恢复原 Profile")
    account.status = body.status
    account.confirmed_at = datetime.now(UTC) if body.status == "confirmed" else None
    account.confirmed_by = identity.subject if body.status == "confirmed" else None
    await db.commit()
    return ApiResponse.ok(account_read(account, instance, get_pool()))


@router.delete("/{account_id}", dependencies=[Depends(account_operation, scope="function")])
async def remove_account(
    account_id: str, body: BrowserAccountDelete, db: AsyncSession = Depends(get_db)
):
    account = await account_or_404(db, account_id)
    from backend.browser_pool import LocalBrowserPool

    if body.clear_login_data and not isinstance(get_pool(), LocalBrowserPool):
        raise HTTPException(409, "当前分布式浏览器池不支持自动清理，请选择保留登录数据")
    instance = await db.get(BrowserInstance, account.browser_instance_id)
    if instance is None:
        raise HTTPException(409, "登录环境不存在，请修复关联后再删除")
    if account.status == "deleting" and account.deletion_clear != body.clear_login_data:
        raise HTTPException(409, "删除已开始，请按原来的登录数据处理方式重试")
    instance.login_reserved = True
    account.status = "deleting"
    account.deletion_clear = body.clear_login_data
    await db.commit()
    if body.clear_login_data:
        try:
            await browser_login_display.run_driver(
                browser_login_display.clear_managed_profile, instance
            )
            from backend.api.v1.browser_containers import update_env_file

            update_env_file(
                "AGENT_POOL_ENDPOINTS",
                ",".join(ep for ep in get_pool().endpoints if ep != instance.endpoint),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "登录数据未能完全清理，请重试删除；账号环境仍被保留") from exc
    await db.delete(account)
    await db.flush()
    if body.clear_login_data:
        instance.login_reserved = False
        await db.flush()
        await db.delete(instance)
    await db.commit()
    if body.clear_login_data:
        from backend.browser_pool import LocalBrowserPool

        pool = get_pool()
        if isinstance(pool, LocalBrowserPool):
            pool.remove_endpoint(instance.endpoint)
    return ApiResponse.ok(None)


@router.post("/{account_id}/restore", dependencies=[Depends(account_operation, scope="function")])
async def restore_account(account_id: str, db: AsyncSession = Depends(get_db)):
    account = await account_or_404(db, account_id)
    if account.status != "archived":
        raise HTTPException(409, "账号未归档")
    account.status = "unconfirmed"
    await db.commit()
    return ApiResponse.ok(None)
