import asyncio
import contextlib
import shutil
import time

from . import browser, config

# What the user completes in the login window.
SIGN_IN = "U-M Okta sign-in and Okta Verify two-factor"


class NeedsLogin(Exception):
    pass


_login_task: asyncio.Task | None = None
_last_result: dict | None = None


def login_active() -> bool:
    return _login_task is not None and not _login_task.done()


def last_login_result() -> dict | None:
    return _last_result


async def await_login(timeout: float | None = None) -> dict | None:
    """Block until an in-flight login finishes, so a fetch issued right after
    login just waits for the user to sign in instead of erroring out."""
    if not login_active():
        return _last_result
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(
            asyncio.shield(_login_task), timeout or config.LOGIN_WAIT_S
        )
    return _last_result


async def check_auth() -> bool:
    """Probe the proxy with a canary URL in a headless context."""
    async with browser.session(headless=True) as ctx:
        page = await ctx.new_page()
        try:
            await page.goto(
                config.proxied(config.CANARY_URL),
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except Exception:
            return False
        await asyncio.sleep(1)
        return browser.is_proxied_url(page.url)


async def _login_flow() -> dict:
    async with browser.session(headless=False) as ctx:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(config.proxied(config.CANARY_URL), timeout=60_000)
        deadline = time.monotonic() + config.LOGIN_TIMEOUT_S
        result = {
            "authenticated": False,
            "message": f"timed out after ~{config.LOGIN_TIMEOUT_S // 60} min waiting for {SIGN_IN}; run login again",
        }
        while time.monotonic() < deadline:
            if page.is_closed():
                result = {
                    "authenticated": False,
                    "message": "browser window was closed before login finished; run login again",
                }
                break
            if browser.is_proxied_url(page.url):
                await asyncio.sleep(2)  # let EZproxy finish setting cookies
                result = {
                    "authenticated": True,
                    "message": "login complete; session saved (the window closes itself)",
                }
                break
            await asyncio.sleep(2)
    # returned only after the context is closed, so a close error on the way
    # out can't overwrite a successful result
    return result


async def _run_login() -> None:
    global _last_result
    try:
        _last_result = await _login_flow()
    except Exception as e:
        _last_result = {"authenticated": False, "message": f"login failed: {e}"}


def start_login() -> dict:
    """Start the interactive login in the background and return immediately,
    so the tool call never outlives an MCP client timeout while the user
    completes sign-in at their own pace.
    """
    global _login_task
    if login_active():
        return {
            "started": False,
            "message": f"a login window is already open; complete {SIGN_IN} there",
        }
    _login_task = asyncio.get_running_loop().create_task(_run_login())
    return {
        "started": True,
        "message": (
            f"browser window opening. Complete {SIGN_IN} in it within about "
            f"{config.LOGIN_TIMEOUT_S // 60} minutes; it closes itself when done. "
            f"The next fetch waits for this automatically, so just go ahead and "
            f"ask for the paper."
        ),
    }


def clear_session() -> dict:
    if login_active() or browser.session_active():
        return {
            "cleared": False,
            "message": "a browser session is in use; finish or close it first",
        }
    p = config.PROFILE_DIR
    if not (config.PROFILE_MARKER.exists() and p.is_relative_to(p.home())):
        return {
            "cleared": False,
            "message": f"refusing to delete {p}: not a profile this tool created",
        }
    shutil.rmtree(p, ignore_errors=True)
    return {"cleared": True, "message": f"library session cleared from {p}"}
