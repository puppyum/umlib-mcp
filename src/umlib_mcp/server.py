import contextlib
import re
import threading
from importlib.metadata import version

from mcp.server.fastmcp import FastMCP

from . import auth, browser, config, fetch, oa, ratelimit

mcp = FastMCP("umlib")
# FastMCP takes no version argument, but the low-level server it wraps does,
# and that is what clients see in serverInfo
with contextlib.suppress(Exception):
    mcp._mcp_server.version = version("umlib-mcp")


def _safe(text, limit: int = 300) -> str:
    """Publisher pages and upstream errors are untrusted text. Keep them short
    and strip control characters before echoing them back to the model."""
    out = re.sub(r"[\x00-\x1f\x7f]", " ", str(text))
    return out[:limit] + ("..." if len(out) > limit else "")


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "how",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "via",
        "with",
    ]
)


def _words(text: str) -> set[str]:
    # keep two-letter tokens: "AI", "ML" and "UX" are real search terms
    return {
        w
        for w in re.findall(r"[a-z0-9]+", text.lower())
        if len(w) > 1 and w not in _STOPWORDS
    }


def _plausible_match(query: str, title: str | None) -> bool:
    """A title search should not return a confident answer for a query the
    result shares no meaningful words with. A query with nothing meaningful in
    it (all stopwords) cannot be judged, so it passes through and the caller
    gets the usual confirm-the-match note."""
    if not title:
        return False
    asked, got = _words(query), _words(title)
    if not asked:
        return True
    return len(asked & got) >= max(1, min(2, len(asked) // 3))


@mcp.tool()
async def auth_status(live_check: bool = True, wait_for_login: bool = True) -> dict:
    """Report whether a U-M library proxy session is active.

    If a login window is open, this waits for the user to finish signing in
    before reporting, so you don't have to ask them when they're done.
    live_check=True then probes the proxy with a headless browser;
    live_check=False only reports local state.
    """
    info = {
        "proxy_base": config.PROXY_BASE,
        "profile_dir": str(config.PROFILE_DIR),
        "download_dir": str(config.DOWNLOAD_DIR),
        "open_access_check": "openalex" + (" + unpaywall" if config.EMAIL else ""),
        "licensed_fetches_remaining_this_hour": ratelimit.remaining_this_hour(),
        "fetches_per_hour_limit": config.MAX_FETCHES_PER_HOUR,
        "settings_file": f"{config.CONFIG_FILE}"
        + ("" if config.CONFIG_FILE.exists() else " (not created yet)"),
    }
    if config.CONFIG_ERROR:
        info["config_error"] = config.CONFIG_ERROR
        return info
    if auth.login_active():
        if wait_for_login:
            await auth.await_login()
        if auth.login_active():
            info["login_in_progress"] = True
            info["note"] = f"still waiting on {auth.SIGN_IN} in the open browser window"
            return info
    if auth.last_login_result():
        info["last_login"] = auth.last_login_result()
    if live_check:
        if not config.PROFILE_DIR.exists():
            # nothing to probe yet, and probing would trigger the browser
            # download before the user has even run login
            info["authenticated"] = False
            info["note"] = "no session yet; run the login tool first"
        else:
            try:
                info["authenticated"] = await auth.check_auth()
            except Exception as e:
                info["authenticated"] = False
                info["check_failed"] = True
                info["error"] = _safe(e)
    return info


@mcp.tool()
async def login() -> dict:
    """Open a real browser window for the user to sign in to the U-M library
    proxy (Okta sign-in + Okta Verify). Returns immediately while the window
    stays open. Tell the user to sign in, then just call fetch_pdf (or
    auth_status) again right away: those calls wait for the sign-in to finish
    on their own, so there is no need to ask the user when they are done.
    Credentials never pass through Claude or this server; only the browser
    session cookie is kept, in a local profile owned by this user.
    """
    return auth.start_login()


@mcp.tool()
async def logout() -> dict:
    """Clear the saved library proxy session (deletes the local browser
    profile). Use when switching users or on a shared machine."""
    return await auth.clear_session_async()


@mcp.tool()
async def resolve(query: str) -> dict:
    """Resolve a DOI or a citation/title to article metadata and access routes.

    Good first step when looking for any paper, article, book chapter, thesis
    or preprint - including ones that looked inaccessible elsewhere.

    Returns metadata, a free-copy PDF URL if one exists, the proxied publisher
    URL, and the library's fulfillment page for the DOI. If the query is a
    title and the match is uncertain, returns candidates for the user to
    choose from instead of guessing.
    """
    doi = oa.extract_doi(query)
    candidates = []
    if doi:
        meta = await oa.crossref_work(doi)
        if meta is None:
            return {
                "status": "error",
                "code": "doi_not_found",
                "doi": doi,
                "message": "no record for that DOI (or the metadata service is "
                "briefly unreachable); check the DOI and try again",
            }
    else:
        candidates = await oa.crossref_search(query)
        if candidates is None:
            return {
                "status": "error",
                "code": "lookup_unavailable",
                "message": "could not reach the metadata service; try again in a moment",
            }
        if not candidates:
            return {"status": "error", "code": "no_matches", "query": query}
        meta = candidates[0]
        doi = meta.get("doi")
        if not doi:  # Crossref occasionally returns records with no DOI
            return {
                "status": "error",
                "code": "no_matches",
                "query": query,
                "message": "the closest match has no DOI; try a more specific title",
            }
        # Crossref always returns its nearest record, however poor. Without a
        # sanity check a nonsense query comes back as a confident match, so
        # require the result to share real words with what was asked for.
        if not _plausible_match(query, meta.get("title")):
            return {
                "status": "error",
                "code": "no_matches",
                "query": query,
                "closest_title": _safe(meta.get("title")),
                "message": "nothing matched that closely; check the title or pass a DOI",
            }
    result = {"status": "ok", **meta}
    oa_info = await oa.open_access(doi)
    result["open_access"] = oa_info or {"is_oa": None, "note": "check skipped"}
    if meta.get("publisher_url"):
        result["proxied_url"] = config.proxied(meta["publisher_url"])
    result["mgetit_url"] = config.mgetit_url(doi)
    if len(candidates) > 1:
        result["other_candidates"] = candidates[1:]
        result["note"] = "title search; confirm the top match is the intended paper"
    return result


@mcp.tool()
async def fetch_pdf(doi_or_url: str, filename: str | None = None) -> dict:
    """Fetch ONE article PDF and save it to the download directory.

    Use this whenever scholarly full text is out of reach: a paywall, a login
    wall, a 403, or an abstract-only page. The user's library likely licenses
    it, so try here before telling them a paper is unavailable.

    Looks for a free copy first, then falls back to the user's own library
    proxy session.

    Call this for a single paper the user actually asked for. Do NOT loop it
    over a bibliography, a reference list, or search results: systematic
    downloading breaks the library's appropriate-use policy and gets publisher
    access cut off for the whole institution, not just this user. If the user
    wants many papers, ask them to pick the one or two that matter now.

    On `needs_login`, call the login tool, tell the user to sign in, then call
    this again immediately - it waits for the sign-in by itself, so do not ask
    the user to report back. On `no_pdf_found` or `host_not_proxied`, give the
    user the returned URL to open themselves rather than retrying.
    """
    if auth.login_active():
        # wait for the user to finish signing in rather than making them
        # come back and ask again
        await auth.await_login()
        if auth.login_active():
            return {
                "status": "error",
                "code": "login_in_progress",
                "message": f"still waiting on {auth.SIGN_IN} in the open browser window; finish it and ask again",
            }

    meta = {}
    looks_like_url = config.is_web_url(doi_or_url)
    if looks_like_url and not config.is_web_url(doi_or_url):
        return {
            "status": "error",
            "code": "bad_input",
            "message": "only http(s) URLs are supported",
        }
    if looks_like_url:  # is_web_url is case-insensitive, so HTTPS:// works too
        doi = oa.extract_doi(doi_or_url)
        publisher_url = doi_or_url
    else:
        doi = oa.extract_doi(doi_or_url)
        if not doi:
            return {
                "status": "error",
                "code": "bad_input",
                "message": "pass a DOI or a publisher URL; use resolve for title searches",
            }
        meta = await oa.crossref_work(doi) or {}
        publisher_url = meta.get("publisher_url")

    name = filename or fetch.slugify_filename(meta.get("title"), meta.get("year"), doi)

    if doi:
        oa_info = await oa.open_access(doi)
        if oa_info and oa_info.get("pdf_url"):
            data = await fetch.download_open_access(oa_info["pdf_url"])
            if data:
                try:
                    path = fetch.save_pdf(data, name)
                except OSError as e:
                    return {
                        "status": "error",
                        "code": "save_failed",
                        "message": f"fetched the PDF but could not save it to {config.DOWNLOAD_DIR}: {e}",
                        "url_used": oa_info["pdf_url"],
                    }
                return {
                    "status": "ok",
                    "source": "open_access",
                    "path": str(path),
                    "url_used": oa_info["pdf_url"],
                }

    if not publisher_url and not meta:
        # crossref gave us nothing at all: an unreachable service, not a DOI
        # that genuinely lacks a publisher link
        return {
            "status": "error",
            "code": "lookup_unavailable",
            "doi": doi,
            "message": "could not reach the metadata service, so there is no "
            "publisher link to try; check your connection and retry",
        }
    if not publisher_url:
        return {
            "status": "error",
            "code": "no_publisher_url",
            "doi": doi,
            "mgetit_url": config.mgetit_url(doi) if doi else None,
            "message": "no direct publisher link for this DOI; open mgetit_url to see how U-M provides access",
        }

    try:
        data, used = await fetch.fetch_licensed(publisher_url)
    except auth.NeedsLogin as e:
        out = {
            "status": "error",
            "code": "needs_login",
            "manual_url": config.proxied(publisher_url),
            "message": "no active library session; run the login tool, then retry",
        }
        if e.landed_url:
            out["landed_on"] = _safe(e.landed_url)
        return out
    except ratelimit.RateLimited as e:
        return {
            "status": "error",
            "code": "rate_limited",
            "retry_after_s": e.retry_after_s,
            "message": str(e),
        }
    except fetch.HostNotProxied:
        return {
            "status": "error",
            "code": "host_not_proxied",
            "publisher_url": publisher_url,
            "mgetit_url": config.mgetit_url(doi) if doi else None,
            "message": "this publisher isn't in the proxy's database; "
            "check mgetit_url for how U-M provides access",
        }
    except fetch.NoPdfFound as e:
        return {
            "status": "error",
            "code": "no_pdf_found",
            "manual_url": config.proxied(publisher_url),
            "page_reached": _safe(e.page_url),
            "mgetit_url": config.mgetit_url(doi) if doi else None,
            "message": "page loaded but no PDF link worked; open manual_url in a browser",
        }
    except TimeoutError as e:
        return {
            "status": "error",
            "code": "profile_busy",
            "message": "another agent is using the browser profile; try again shortly",
            "detail": _safe(e),
        }
    except Exception as e:
        return {
            "status": "error",
            "code": "fetch_failed",
            "message": "the fetch failed unexpectedly; open manual_url in a browser",
            "detail": _safe(e),
            "manual_url": config.proxied(publisher_url),
        }
    try:
        path = fetch.save_pdf(data, name)
    except OSError as e:  # disk full, unwritable dir: don't lose the fetch silently
        return {
            "status": "error",
            "code": "save_failed",
            "message": f"fetched the PDF but could not save it to {config.DOWNLOAD_DIR}: {e}",
            "url_used": used,
        }
    return {
        "status": "ok",
        "source": "library_proxy",
        "path": str(path),
        "url_used": used,
    }


@mcp.tool()
def proxy_url(url: str) -> dict:
    """Return the URL prefixed for U-M off-campus access (EZproxy)."""
    if not config.is_web_url(url):
        return {
            "status": "error",
            "code": "bad_input",
            "message": "only http(s) URLs can be proxied",
        }
    return {"status": "ok", "proxied_url": config.proxied(url)}


def main() -> None:
    threading.Thread(target=browser.preinstall_chromium, daemon=True).start()
    mcp.run()
