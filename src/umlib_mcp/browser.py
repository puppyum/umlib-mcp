import asyncio
import contextlib
import json
import os
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from playwright.async_api import async_playwright

from . import config

_pw = None
_lock = asyncio.Lock()


async def _playwright():
    global _pw
    if _pw is None:
        _pw = await async_playwright().start()
    return _pw


_install_lock = threading.Lock()


def _install_chromium() -> None:
    # idempotent and fast when the browser is already in the shared cache;
    # the lock keeps the startup preinstall and a first tool call from
    # downloading concurrently
    with _install_lock:
        proc = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
    if proc.returncode != 0:
        raise RuntimeError(
            f"playwright install chromium failed: {proc.stderr.strip()[-400:]}"
        )


def preinstall_chromium() -> None:
    """Run at server start in a background thread, so the browser is already
    downloaded by the time a first-time user runs login."""
    with contextlib.suppress(Exception):
        _install_chromium()


def ensure_profile_dir() -> None:
    config.PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(config.PROFILE_DIR, 0o700)
    config.PROFILE_MARKER.touch(exist_ok=True)


async def _launch(headless: bool):
    pw = await _playwright()
    ensure_profile_dir()
    kwargs: dict = {"headless": headless, "accept_downloads": True}
    if headless:
        kwargs["viewport"] = {"width": 1280, "height": 900}
    try:
        return await pw.chromium.launch_persistent_context(
            str(config.PROFILE_DIR), **kwargs
        )
    except Exception as e:
        if "Executable doesn't exist" not in str(e):
            raise
        await asyncio.to_thread(_install_chromium)
        return await pw.chromium.launch_persistent_context(
            str(config.PROFILE_DIR), **kwargs
        )


async def _restore_state(ctx) -> None:
    if not config.STATE_FILE.exists():
        return
    with contextlib.suppress(Exception):
        cookies = json.loads(config.STATE_FILE.read_text()).get("cookies", [])
        if cookies:
            await ctx.add_cookies(cookies)


async def _save_state(ctx) -> None:
    with contextlib.suppress(Exception):
        state = await ctx.storage_state()
        if state.get("cookies"):
            config.STATE_FILE.write_text(json.dumps(state))
            os.chmod(config.STATE_FILE, 0o600)


@asynccontextmanager
async def session(headless: bool = True):
    # One browser operation at a time: the persistent profile cannot be
    # shared by concurrent contexts, and single-flight is the polite mode.
    async with _lock:
        ctx = await _launch(headless)
        await _restore_state(ctx)
        try:
            yield ctx
        finally:
            # session cookies live only in memory, so snapshot them before
            # the context goes away; a close error must never overwrite a
            # good result the caller already produced inside the context
            await _save_state(ctx)
            with contextlib.suppress(Exception):
                await ctx.close()


def session_active() -> bool:
    """True while a browser context (headless fetch or headed login) is open."""
    return _lock.locked()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_proxied_url(url: str) -> bool:
    """True once EZproxy has rewritten us onto a licensed host."""
    host = host_of(url)
    return host.endswith("." + config.PROXY_HOST) or (
        host == config.PROXY_HOST and not urlparse(url).path.startswith("/login")
    )
