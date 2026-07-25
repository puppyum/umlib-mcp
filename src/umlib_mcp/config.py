import os
from pathlib import Path
from urllib.parse import urlparse

# EZproxy prefix. U-M documents this exact pattern as the supported public
# interface ("Create Links that Work"). Override for other institutions.
PROXY_BASE = os.environ.get(
    "UMLIB_PROXY_BASE", "https://proxy.lib.umich.edu/login?url="
)
PROXY_HOST = urlparse(PROXY_BASE).hostname or "proxy.lib.umich.edu"

PROFILE_DIR = Path(os.environ.get("UMLIB_PROFILE_DIR", "~/.umlib/profile")).expanduser()
DOWNLOAD_DIR = Path(os.environ.get("UMLIB_DOWNLOAD_DIR", "~/Downloads")).expanduser()

# A file we drop inside a profile dir we created, so logout only ever deletes
# a directory this tool owns (not, say, a user's real folder that happens to
# sit at a custom UMLIB_PROFILE_DIR).
PROFILE_MARKER = PROFILE_DIR / ".umlib-managed"

# EZproxy and Okta issue session cookies, which Chromium keeps in memory and
# drops when the context closes. We snapshot them here so a later fetch can
# reuse the session the user signed into. Same sensitivity as the profile
# itself: mode 600 inside a 700 directory, removed by logout.
STATE_FILE = PROFILE_DIR / "session-state.json"

# Optional. Open-access lookups work without it (OpenAlex needs no contact
# address); setting it adds Unpaywall as a second source, which requires one.
EMAIL = os.environ.get("UMLIB_EMAIL", "")

# Licensed-fetch pacing. Individual on-demand retrieval only; U-M's
# "Appropriate Use of Electronic Resources" statement prohibits systematic
# downloading, and publishers block on volume anomalies.
MAX_FETCHES_PER_HOUR = int(os.environ.get("UMLIB_MAX_FETCHES_PER_HOUR", "8"))
MIN_FETCH_INTERVAL_S = float(os.environ.get("UMLIB_MIN_FETCH_INTERVAL_S", "20"))

# Reject anything larger than this before saving; a PDF article is a few MB.
MAX_PDF_BYTES = int(os.environ.get("UMLIB_MAX_PDF_BYTES", str(100 * 1024 * 1024)))

LOGIN_TIMEOUT_S = int(os.environ.get("UMLIB_LOGIN_TIMEOUT_S", "300"))
# How long a fetch will sit waiting for an in-flight sign-in before giving up
# and asking the user to retry.
LOGIN_WAIT_S = float(os.environ.get("UMLIB_LOGIN_WAIT_S", "150"))
CANARY_URL = os.environ.get("UMLIB_CANARY_URL", "https://www.jstor.org/")

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
