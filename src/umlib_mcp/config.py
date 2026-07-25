import os
import re
import tomllib
from pathlib import Path
from urllib.parse import urlparse

# Problems found while reading settings. Collected rather than raised: raising
# at import stops the server from starting at all, and the user sees nothing
# but their agent failing to connect. The tools report these instead.
_PROBLEMS: list[str] = []


# Settings come from ~/.umlib/config.toml, and an environment variable beats
# the file when both are set. The file exists because environment variables
# are awkward to apply across agents: Codex clears the environment to a fixed
# allowlist, and Claude Desktop needs them written into JSON by hand. One file
# changes the behaviour for every agent at once.
def _config_file() -> Path:
    """expanduser() raises on an unknown ~user, and this runs before anything
    else in the module, so an unusable UMLIB_CONFIG would take the server down
    on the very first line."""
    raw = os.environ.get("UMLIB_CONFIG", "~/.umlib/config.toml")
    for candidate in (raw, "~/.umlib/config.toml"):
        try:
            return Path(candidate).expanduser()
        except (RuntimeError, OSError, ValueError) as e:
            if candidate == raw:
                _PROBLEMS.append(f"UMLIB_CONFIG is not a usable path ({raw!r}: {e})")
    return Path(".umlib-config.toml").absolute()


CONFIG_FILE = _config_file()


def _load_file() -> dict:
    """Never raises. A file we could not use is recorded, because silently
    running on defaults is a hard thing to debug from the other end."""
    try:
        with open(CONFIG_FILE, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return {}  # the ordinary case: no config file, all defaults
    except OSError as e:
        _PROBLEMS.append(
            f"could not read {CONFIG_FILE} ({e.strerror or e}); using defaults"
        )
        return {}
    except tomllib.TOMLDecodeError as e:
        _PROBLEMS.append(f"{CONFIG_FILE} is not valid TOML ({e}); using defaults")
        return {}
    if not isinstance(data, dict):
        _PROBLEMS.append(f"{CONFIG_FILE} is not a table of settings; using defaults")
        return {}
    return data


_FILE = _load_file()

# Settings go at the top level. Putting them all under a [header] is the common
# TOML mistake, and having every one of them silently ignored is a bad way to
# find that out.
if _FILE and all(isinstance(v, dict) for v in _FILE.values()):
    _PROBLEMS.append(
        f"every setting in {CONFIG_FILE} sits under a [section] header, but umlib "
        "reads top-level keys only, so none of them took effect"
    )


def setting(key: str, default, cast=str, allow_empty: bool = False):
    """Resolve one setting: environment first, then the config file, then the
    built-in default. A bad value anywhere falls through instead of raising."""
    env = "UMLIB_" + key.upper()
    sources = (
        ("the environment", os.environ.get(env)),
        (str(CONFIG_FILE), _FILE.get(key)),
    )
    for source, raw in sources:
        if raw is None:
            continue
        if isinstance(raw, str):
            raw = raw.strip()  # a trailing newline in a config file is invisible
        if raw == "":
            # an explicit empty value means "switch this off" where that is
            # supported, and "not set" everywhere else
            if allow_empty:
                return ""
            continue
        # TOML hands back real types, and a bool would sail straight through
        # int() as 0 or 1 while a list would str() into something meaningless
        if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
            _PROBLEMS.append(
                f"{key} in {source} is not a usable value ({raw!r}); using the default"
            )
            continue
        try:
            return cast(raw)
        except (ValueError, TypeError):
            _PROBLEMS.append(
                f"{key} in {source} is not a usable value ({raw!r}); using the default"
            )
    return default


def _path(key: str, default: str) -> Path:
    """Never raises: an unusable path setting must not stop the server from
    starting. expanduser() raises on an unknown ~user and resolve() raises on a
    symlink loop, both of which are things a copied config file can carry."""
    configured = setting(key, default)
    for candidate in (configured, default):
        try:
            p = Path(candidate).expanduser()
            # relative paths resolve against home, not the process working
            # directory: for an MCP server that is wherever the agent happened
            # to be started, which is not somewhere to put someone's PDFs
            if not p.is_absolute():
                p = Path.home() / p
            return p.resolve()
        except (RuntimeError, OSError, ValueError) as e:
            if candidate == configured:
                _PROBLEMS.append(
                    f"{key} is not a usable path ({candidate!r}: {e}); using {default}"
                )
    # Last resort, when even home cannot be resolved. Not Path(default), which
    # would put a directory literally named "~" wherever the agent started.
    _PROBLEMS.append(f"could not resolve a home directory for {key}")
    return Path(f".umlib-{key}").absolute()


# EZproxy prefix. U-M documents this exact pattern as the supported public
# interface ("Create Links that Work"). Override for other institutions.
PROXY_BASE = setting("proxy_base", "https://proxy.lib.umich.edu/login?url=")


def _proxy_host(base: str) -> str:
    """A bad proxy_base must not silently fall back to U-M, and must not take
    the server down either: report it and let the tools refuse to run."""
    try:
        parts = urlparse(base)
    except ValueError as e:
        # an unbalanced "[" is an "Invalid IPv6 URL", and urlparse raises it
        _PROBLEMS.append(f"proxy_base is not a usable URL ({base!r}: {e})")
        return ""
    if parts.scheme.lower() != "https":
        _PROBLEMS.append(
            f"proxy_base must start with https:// (got {base!r}); over plain http "
            "the library session cookie would travel in the clear"
        )
        return ""
    host = (parts.hostname or "").lower()
    if not host:
        _PROBLEMS.append(
            f"proxy_base has no hostname: {base!r} "
            "(it should look like https://proxy.your-school.edu/login?url=)"
        )
    return host


PROXY_HOST = _proxy_host(PROXY_BASE)


# EZproxy rewrites a licensed site onto a subdomain of the proxy host
# (www-jstor-org.proxy.lib.umich.edu). OCLC-hosted schools are the common
# exception: they sign in at login.<school>.idm.oclc.org but rewrite onto
# <host>.<school>.idm.oclc.org, so the login label has to be dropped.
def _default_rewrite_host(proxy_host: str) -> str:
    if proxy_host.startswith("login.") and proxy_host.endswith(".idm.oclc.org"):
        return proxy_host[len("login.") :]
    return proxy_host


REWRITE_HOST = setting("rewrite_host", _default_rewrite_host(PROXY_HOST)).lower()

# U-M's link resolver. Schools using another resolver can point this elsewhere;
# an empty value omits the fulfillment link from results, which is why this one
# accepts an explicit "" rather than reading it as "unset".
RESOLVER_BASE = setting(
    "resolver_base",
    "https://mgetit.lib.umich.edu/resolve?rft_id=info:doi/",
    allow_empty=True,
)

PROFILE_DIR = _path("profile_dir", "~/.umlib/profile")


def _default_download_dir() -> str:
    """XDG_DOWNLOAD_DIR when the desktop defines it: on a non-English Linux
    desktop ~/Downloads is not where the file manager looks."""
    return os.environ.get("XDG_DOWNLOAD_DIR") or "~/Downloads"


DOWNLOAD_DIR = _path("download_dir", _default_download_dir())

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
_EMAIL_RAW = setting("email", "")
# It goes straight into a User-Agent header, where a stray control character
# makes httpx refuse to send at all and every metadata lookup then surfaces as
# "doi not found". The local part is therefore the RFC atext set plus dot,
# which admits no control characters rather than merely no whitespace.
EMAIL = (
    _EMAIL_RAW.strip()
    if re.fullmatch(
        r"[0-9A-Za-z!#$%&'*+/=?^_`{|}~.-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        _EMAIL_RAW.strip(),
    )
    else ""
)
EMAIL_INVALID = bool(_EMAIL_RAW.strip()) and not EMAIL
if EMAIL_INVALID:
    _PROBLEMS.append(
        f"email is not a usable address ({_EMAIL_RAW.strip()!r}), so Unpaywall is "
        "switched off; open-access lookups still work through OpenAlex"
    )

# Licensed-fetch pacing. The library's appropriate-use statement bars
# systematic downloading but sets no number, so these are our own courtesy
# limits: comfortably above ordinary reading, far below anything that looks
# like a crawler to a publisher.
MAX_FETCHES_PER_HOUR = setting("max_fetches_per_hour", 60, int)
MIN_FETCH_INTERVAL_S = setting("min_fetch_interval_s", 5.0, float)

# Reject anything larger than this before saving; a PDF article is a few MB.
MAX_PDF_BYTES = setting("max_pdf_bytes", 100 * 1024 * 1024, int)

LOGIN_TIMEOUT_S = setting("login_timeout_s", 300, int)
# The interactive login holds the browser profile for its whole window, so a
# fetch queued behind it has to be willing to wait at least that long.
LOCK_TIMEOUT_S = setting("lock_timeout_s", LOGIN_TIMEOUT_S + 60, float)
# How long a fetch will sit waiting for an in-flight sign-in before giving up
# and asking the user to retry.
LOGIN_WAIT_S = setting("login_wait_s", 150.0, float)
CANARY_URL = setting("canary_url", "https://www.jstor.org/")


def _version() -> str:
    # read from package metadata rather than repeating the number here, where
    # it would quietly drift away from pyproject.toml
    try:
        from importlib.metadata import version

        return version("umlib-mcp")
    except Exception:
        return "0"


USER_AGENT = (
    f"umlib-mcp/{_version()} (mailto:{EMAIL})" if EMAIL else f"umlib-mcp/{_version()}"
)

# Everything above has now been read, so this is the complete picture. Empty
# when the configuration is fine.
CONFIG_ERROR = "; ".join(_PROBLEMS)


def unusable() -> str:
    """The subset of CONFIG_ERROR that makes proxying impossible. A bad email
    or an ignored setting is worth reporting but still leaves a working server;
    a proxy_base we cannot parse does not."""
    return CONFIG_ERROR if not PROXY_HOST else ""


def is_web_url(url: str) -> bool:
    """Only http(s) targets are ever proxied or fetched; blocks file:, data:,
    javascript:, etc. from reaching the browser or an httpx client. Schemes are
    case-insensitive, so HTTPS:// is a URL, not a DOI."""
    try:
        return urlparse(url).scheme.lower() in ("http", "https")
    except ValueError:
        return False


def already_proxied(url: str) -> bool:
    return urlparse(url).hostname == PROXY_HOST or (
        urlparse(url).hostname or ""
    ).endswith("." + REWRITE_HOST)


def proxied(url: str) -> str:
    if already_proxied(url):
        return url  # don't wrap a proxy URL in another proxy URL
    # The target goes in raw, exactly as the library documents it. Do not be
    # tempted to percent-encode it: EZproxy reads everything after url=
    # literally and does its own encoding downstream, so an encoded target
    # arrives empty. Verified against the live proxy with a target carrying
    # both ? and & - raw round-trips intact, encoded yields an empty qurl.
    return PROXY_BASE + url


def mgetit_url(doi: str) -> str:
    """Link to the library's fulfillment page. Empty when no resolver is set."""
    from urllib.parse import quote

    return RESOLVER_BASE + quote(doi, safe="/.") if RESOLVER_BASE else ""
