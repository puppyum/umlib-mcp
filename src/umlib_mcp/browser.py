import asyncio
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import threading
import time
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


_held_locks: set[int] = set()


def _lock_path():
    return config.PROFILE_DIR.parent / "umlib.lock"


def _acquire_file_lock(timeout: float | None = None):
    """Chromium cannot share one profile between processes, and several agents
    may each be running their own copy of this server. A lock file makes them
    take turns instead of corrupting the profile."""
    timeout = config.LOCK_TIMEOUT_S if timeout is None else timeout
    _lock_path().parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(_lock_path(), os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            _held_locks.add(fd)
            return fd
        except OSError:
            if time.monotonic() > deadline:
                os.close(fd)
                raise TimeoutError(
                    "another umlib server is using the browser profile; try again shortly"
                ) from None
            time.sleep(0.5)


def _release_file_lock(fd) -> None:
    # idempotent: closing a stale fd number could unlock or close something
    # else entirely that has since been assigned that descriptor
    if fd not in _held_locks:
        return
    _held_locks.discard(fd)
    with contextlib.suppress(Exception):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(Exception):
        os.close(fd)


@asynccontextmanager
async def session(headless: bool = True):
    # One browser operation at a time: the persistent profile cannot be shared
    # by concurrent contexts. _lock serializes within this process; the lock
    # file serializes across the other agents' server processes.
    async with _lock:
        # shield the acquire: if we are cancelled while the worker thread is
        # still blocking on flock, the thread may go on to take the lock, and
        # an abandoned fd would wedge every later process on this profile
        acquiring = asyncio.create_task(asyncio.to_thread(_acquire_file_lock))
        try:
            fd = await asyncio.shield(acquiring)
        except BaseException:
            acquiring.add_done_callback(
                lambda t: (
                    _release_file_lock(t.result())
                    if not t.cancelled() and t.exception() is None
                    else None
                )
            )
            raise
        try:
            ctx = await _launch(headless)
            await _restore_state(ctx)
        except BaseException:
            _release_file_lock(fd)
            raise
        try:
            yield ctx
        finally:
            # the release needs its own finally: a client cancelling a tool
            # call re-raises at the first await in here, and anything that
            # escapes would strand the lock and wedge every umlib process
            try:
                await _save_state(ctx)
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(ctx.close(), timeout=30)
            finally:
                _release_file_lock(fd)


def session_active() -> bool:
    """True while a browser context (headless fetch or headed login) is open."""
    return _lock.locked()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_proxied_url(url: str) -> bool:
    """True once EZproxy has rewritten us onto a licensed host."""
    host = host_of(url)
    if host.endswith("." + config.REWRITE_HOST):
        return True
    # the proxy's own domain counts too, except for its login/auth pages
    return host == config.PROXY_HOST and not urlparse(url).path.startswith(
        ("/login", "/menu/login", "/logout")
    )
