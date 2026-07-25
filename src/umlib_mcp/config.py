import os
import tomllib
from pathlib import Path
from urllib.parse import urlparse

# Settings come from ~/.umlib/config.toml, and an environment variable beats
# the file when both are set. The file exists because environment variables
# are awkward to apply across agents: Codex clears the environment to a fixed
# allowlist, and Claude Desktop needs them written into JSON by hand. One file
# changes the behaviour for every agent at once.
CONFIG_FILE = Path(os.environ.get("UMLIB_CONFIG", "~/.umlib/config.toml")).expanduser()


def _load_file() -> dict:
    try:
        with open(CONFIG_FILE, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}  # missing or malformed: fall back to defaults, never crash
    return data if isinstance(data, dict) else {}


_FILE = _load_file()


def setting(key: str, default, cast=str):
    """Resolve one setting: environment first, then the config file, then the
    built-in default. A bad value anywhere falls through instead of raising."""
    env = "UMLIB_" + key.upper()
    for raw in (os.environ.get(env), _FILE.get(key)):
        if raw is None or raw == "":
            continue
        try:
            return cast(raw)
        except (ValueError, TypeError):
            continue
    return default


def _path(key: str, default: str) -> Path:
    # resolve() so a relative override becomes absolute; the logout guard and
    # the lock file both depend on knowing where this really points
    return Path(setting(key, default)).expanduser().resolve()


# EZproxy prefix. U-M documents this exact pattern as the supported public
# interface ("Create Links that Work"). Override for other institutions.
PROXY_BASE = setting("proxy_base", "https://proxy.lib.umich.edu/login?url=")
PROXY_HOST = urlparse(PROXY_BASE).hostname or "proxy.lib.umich.edu"

PROFILE_DIR = _path("profile_dir", "~/.umlib/profile")
DOWNLOAD_DIR = _path("download_dir", "~/Downloads")

# A file we drop inside a profile dir we created, so logout only ever deletes
# a directory this tool owns (not, say, a user's real folder that happens to
# sit at a custom profile_dir).
PROFILE_MARKER = PROFILE_DIR / ".umlib-managed"

# EZproxy and Okta issue session cookies, which Chromium keeps in memory and
# drops when the context closes. We snapshot them here so a later fetch can
# reuse the session the user signed into. Same sensitivity as the profile
# itself: mode 600 inside a 700 directory, removed by logout.
STATE_FILE = PROFILE_DIR / "session-state.json"

# Optional. Open-access lookups work without it (OpenAlex needs no contact
# address); setting it adds Unpaywall as a second source, which requires one.
EMAIL = setting("email", "")

# Licensed-fetch pacing. The library's appropriate-use statement bars
# systematic downloading but sets no number, so these are our own courtesy
# limits: comfortably above ordinary reading, far below anything that looks
# like a crawler to a publisher.
MAX_FETCHES_PER_HOUR = setting("max_fetches_per_hour", 60, int)
MIN_FETCH_INTERVAL_S = setting("min_fetch_interval_s", 5.0, float)

# Reject anything larger than this before saving; a PDF article is a few MB.
MAX_PDF_BYTES = setting("max_pdf_bytes", 100 * 1024 * 1024, int)

LOGIN_TIMEOUT_S = setting("login_timeout_s", 300, int)
# How long a fetch will sit waiting for an in-flight sign-in before giving up
# and asking the user to retry.
LOGIN_WAIT_S = setting("login_wait_s", 150.0, float)
CANARY_URL = setting("canary_url", "https://www.jstor.org/")

MGETIT_BASE = "https://mgetit.lib.umich.edu/resolve?rft_id=info:doi/"

USER_AGENT = f"umlib-mcp/0.1 (mailto:{EMAIL})" if EMAIL else "umlib-mcp/0.1"


def is_web_url(url: str) -> bool:
    """Only http(s) targets are ever proxied or fetched; blocks file:, data:,
    javascript:, etc. from reaching the browser or an httpx client."""
    try:
        return urlparse(url).scheme in ("http", "https")
    except ValueError:
        return False


def proxied(url: str) -> str:
    # The target goes in raw, exactly as the library documents it. Do not be
    # tempted to percent-encode it: EZproxy reads everything after url=
    # literally and does its own encoding downstream, so an encoded target
    # arrives empty. Verified against the live proxy with a target carrying
    # both ? and & - raw round-trips intact, encoded yields an empty qurl.
    return PROXY_BASE + url


def mgetit_url(doi: str) -> str:
    return MGETIT_BASE + doi
