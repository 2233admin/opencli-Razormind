"""Explicit account-to-Profile ownership and honest, time-bounded confirmation."""

import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.browser import BrowserInstance
from backend.models.browser_account import BrowserAccount

PLATFORMS = {
    "douyin": {"label": "抖音", "url": "https://www.douyin.com/"},
    "xiaohongshu": {"label": "小红书", "url": "https://www.xiaohongshu.com/"},
    "youtube": {"label": "YouTube", "url": "https://www.youtube.com/"},
    "bilibili": {"label": "哔哩哔哩", "url": "https://www.bilibili.com/"},
    "weibo": {"label": "微博", "url": "https://weibo.com/"},
    "zhihu": {"label": "知乎", "url": "https://www.zhihu.com/"},
    "github": {"label": "GitHub", "url": "https://github.com/login"},
    "x": {"label": "X", "url": "https://x.com/"},
}


def website_url(account) -> str:
    return getattr(account, "site_url", None) or PLATFORMS.get(account.platform, {}).get(
        "url", f"https://{account.platform}/"
    )


async def resolve_website(site_url: str | None, platform: str | None) -> tuple[str, str]:
    from backend.security.url_guard import SSRFValidationError, avalidate_public_url

    url = site_url or PLATFORMS.get(platform or "", {}).get("url") or platform or ""
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise HTTPException(422, "请使用不含密码或片段的网站地址")
    try:
        await avalidate_public_url(url)
    except SSRFValidationError as exc:
        raise HTTPException(422, "请输入可公开访问的 HTTP 或 HTTPS 网站地址") from exc
    return url, (parsed.hostname or "").lower().removeprefix("www.")


async def require_unassigned(db: AsyncSession, instance_id: str) -> None:
    # Serialize with account creation on PostgreSQL. SQLite additionally uses
    # narrow migration triggers, without changing global foreign-key behavior.
    instance = await db.scalar(
        select(BrowserInstance).where(BrowserInstance.id == instance_id).with_for_update()
    )
    account = await db.scalar(
        select(BrowserAccount.id).where(BrowserAccount.browser_instance_id == instance_id)
    )
    if account or (instance and instance.login_reserved):
        raise HTTPException(409, "该浏览器保存着账号登录数据，不能重新分配或修改登录环境。")


async def account_or_404(db: AsyncSession, account_id: str) -> BrowserAccount:
    account = await db.get(BrowserAccount, account_id)
    if account is None:
        raise HTTPException(404, "账号不存在")
    return account


def account_read(account: BrowserAccount, instance: BrowserInstance | None, pool) -> dict:
    from backend.config import get_settings

    profile_matches = bool(
        instance
        and instance.profile_name == account.profile_name
        and instance.profile_kind == "authenticated"
    )
    registered = bool(instance and instance.endpoint in pool.endpoints)
    state = account.status
    confirmed_at = account.confirmed_at
    if confirmed_at and confirmed_at.tzinfo is None:
        confirmed_at = confirmed_at.replace(tzinfo=UTC)
    if not profile_matches:
        state = "profile_changed"
    elif state == "confirmed" and (
        not confirmed_at or datetime.now(UTC) - confirmed_at > timedelta(hours=24)
    ):
        state = "unconfirmed"
    # Only managed agent-N slots have a known local noVNC port. Never route an
    # arbitrary remote endpoint to the first browser's desktop.
    match = (
        re.fullmatch(r"agent(?:-([1-9]\d*))?", urlparse(instance.endpoint).hostname or "")
        if instance
        else None
    )
    port = get_settings().novnc_base_port + (int(match[1] or 1) - 1) if match else None
    return {
        "id": account.id,
        "platform": account.platform,
        "label": account.label,
        "browser_instance_id": account.browser_instance_id,
        "profile_name": account.profile_name,
        "status": state,
        "confirmed_at": confirmed_at,
        "confirmation_source": "operator" if confirmed_at else None,
        "browser_state": (
            "unavailable"
            if not registered or not profile_matches
            else "ready"
            if pool.is_ready(instance.endpoint) and pool.available_for(instance.endpoint)
            else "busy_or_unavailable"
        ),
        "login_url": website_url(account),
        "novnc_port": port if profile_matches and port and port <= 65535 else None,
        "created_at": account.created_at,
    }
