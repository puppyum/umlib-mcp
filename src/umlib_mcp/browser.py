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
_live_contexts = 0


async def _playwright():
    """The driver is a subprocess. If it dies, every later call fails forever
    unless we notice and build a new one."""
    global _pw
    if _pw is not None:
        try:
            _ = _pw.chromium  # cheap liveness probe
        except Exception:
            _pw = None
    if _pw is None:
        _pw = await async_playwright().start()
    return _pw


async def _discard_playwright() -> None:
    """Drop a driver that has stopped responding so the next call rebuilds it."""
    global _pw
    dead, _pw = _pw, None
    if dead is not None:
        with contextlib.suppress(Exception):
            await dead.stop()


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
            stdin=subprocess.DEVNULL,  # never share the JSON-RPC stdin
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
        if "Executable doesn't exist" in str(e):
            await asyncio.to_thread(_install_chromium)
        else:
            # a driver that has died stays dead; rebuild it and try once more
            await _discard_playwright()
            pw = await _playwright()
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


async def _save_state(ctx) -> bool:
    """Returns whether cookies were actually persisted, so callers do not tell
    the user their session was saved when nothing was written."""
    try:
        state = await ctx.storage_state()
    except Exception:
        return False
    if not state.get("cookies"):
        return False
    try:
        # create it private, rather than writing then chmod-ing
        fd = os.open(config.STATE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(state, fh)
    except OSError:
        return False
    return True


_held_locks: set[int] = set()


def _lock_path():
    # A sibling of the profile, not a file inside it: logout deletes that whole
    # tree, and flock binds to the inode, so a lock removed while held lets a
    # waiter keep the deleted inode while the next process acquires the
    # recreated file - two Chromiums on one profile, the exact corruption this
    # lock exists to stop. A sibling also keeps exclusion inode-based, so two
    # processes that spell the same directory differently (a case-insensitive
    # filesystem, a symlinked home) still land on one lock.
    return config.PROFILE_DIR.parent / f".{config.PROFILE_DIR.name}.lock"


def _acquire_file_lock(timeout: float | None = None):
    """Chromium cannot share one profile between processes, and several agents
    may each be running their own copy of this server. A lock file makes them
    take turns instead of corrupting the profile."""
    timeout = config.LOCK_TIMEOUT_S if timeout is None else timeout
    # only the parent: creating PROFILE_DIR here would leave a stray profile
    # behind after logout and defeat the "no session yet" fast path
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


_last_save_ok = False


def last_save_ok() -> bool:
    """Whether the most recent teardown actually wrote cookies to disk, so
    login can avoid reporting a saved session that was not saved."""
    return _last_save_ok


async def _teardown(ctx, fd) -> None:
    """Save cookies, close the browser, then release the lock - in that order,
    and always all three, so the profile is never handed on while in use."""
    global _last_save_ok
    _last_save_ok = False
    try:
        with contextlib.suppress(Exception):
            _last_save_ok = await asyncio.wait_for(_save_state(ctx), timeout=30)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ctx.close(), timeout=30)
    finally:
        _release_file_lock(fd)


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
        ctx = None
        try:
            ctx = await _launch(headless)
            await _restore_state(ctx)
        except BaseException:
            # _restore_state can fail or be cancelled after the launch, which
            # would otherwise leave a live browser on the profile we unlock.
            # The release goes in a finally and the close suppresses
            # BaseException: a CancelledError raised by that await is not an
            # Exception, so suppressing only Exception let it skip the release
            # and strand the flock for the life of the process.
            try:
                if ctx is not None:
                    with contextlib.suppress(BaseException):
                        await asyncio.shield(asyncio.wait_for(ctx.close(), timeout=30))
            finally:
                _release_file_lock(fd)
            raise
        global _live_contexts
        _live_contexts += 1
        try:
            yield ctx
        finally:
            _live_contexts -= 1
            # Teardown runs as one shielded task: a client cancelling a tool
            # call re-raises at the first await here, and if that skipped
            # ctx.close() we would unlock a profile with a live browser still
            # on it. Shielding lets the task finish even when we are cancelled.
            with contextlib.suppress(BaseException):
                await asyncio.shield(asyncio.create_task(_teardown(ctx, fd)))


def session_active() -> bool:
    """True only while a browser context is actually open. _lock.locked() is
    also true for callers merely queued for it, which made logout refuse when
    it was perfectly safe."""
    return _live_contexts > 0


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


AUTH_PATHS = ("/login", "/menu/login", "/logout", "/idp/", "/adfs/", "/oauth2/")


def is_proxied_url(url: str) -> bool:
    """True once EZproxy has rewritten us onto a licensed host.

    A sign-in page never counts, whichever host it is on. Binding that
    exclusion to the proxy host alone was not enough: at an OCLC school the
    sign-in host is a subdomain of the rewrite host, and at a school whose
    proxy rewrites onto the campus apex the SSO provider usually lives there
    too. Either way the sign-in page looked like licensed content, so status
    reported a session nobody had and the login window closed before the user
    had signed in.
    """
    host = host_of(url)
    if host != config.PROXY_HOST and not host.endswith("." + config.REWRITE_HOST):
        return False
    return not urlparse(url).path.startswith(AUTH_PATHS)
