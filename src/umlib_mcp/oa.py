import re
from urllib.parse import quote

import httpx

from . import config

DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)


def extract_doi(text: str) -> str | None:
    m = DOI_RE.search(text or "")
    if not m:
        return None
    doi = m.group(0)
    # trim trailing punctuation, but keep closers that are balanced inside
    # the DOI itself (e.g. 10.1016/0167-2789(92)90242-F)
    prev = None
    while doi != prev:
        prev = doi
        doi = doi.rstrip(".,;")
        for opener, closer in (("(", ")"), ("[", "]"), ("{", "}")):
            while doi.endswith(closer) and doi.count(closer) > doi.count(opener):
                doi = doi[:-1]
    return doi


def parse_crossref(message: dict) -> dict:
    titles = message.get("title") or []
    issued = (message.get("issued") or {}).get("date-parts") or [[None]]
    authors = [
        a.get("family") or a.get("name", "") for a in (message.get("author") or [])[:3]
    ]
    resource = ((message.get("resource") or {}).get("primary") or {}).get("URL")
    return {
        "doi": message.get("DOI"),
        "title": titles[0] if titles else None,
        "year": issued[0][0],
        "venue": (message.get("container-title") or [None])[0],
        "authors": authors,
        "publisher_url": resource or message.get("URL"),
    }


def parse_openalex(j: dict) -> dict:
    oa = j.get("open_access") or {}
    loc = j.get("best_oa_location") or {}
    return {
        "is_oa": bool(oa.get("is_oa")),
        "oa_status": oa.get("oa_status"),
        "pdf_url": loc.get("pdf_url") or None,
    }


def parse_unpaywall(j: dict) -> dict:
    loc = j.get("best_oa_location") or {}
    return {
        "is_oa": bool(j.get("is_oa")),
        "oa_status": j.get("oa_status"),
        "pdf_url": loc.get("url_for_pdf") or loc.get("url"),
    }


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={"User-Agent": config.USER_AGENT},
        timeout=20,
        follow_redirects=True,
    )


def _message(r: httpx.Response) -> dict | None:
    if r.status_code != 200:
        return None
    try:
        return r.json().get("message")
    except (ValueError, AttributeError):
        return None


async def crossref_work(doi: str) -> dict | None:
    async with _client() as c:
        r = await c.get(f"https://api.crossref.org/works/{quote(doi, safe='')}")
        msg = _message(r)
        return parse_crossref(msg) if msg else None


async def crossref_search(query: str, rows: int = 5) -> list[dict]:
    async with _client() as c:
        r = await c.get(
            "https://api.crossref.org/works",
            params={"query.bibliographic": query, "rows": rows},
        )
        msg = _message(r)
        items = msg.get("items", []) if msg else []
        return [parse_crossref(m) | {"score": m.get("score")} for m in items]


async def openalex(doi: str) -> dict | None:
    """Open-access lookup that needs no API key and no contact address."""
    async with _client() as c:
        r = await c.get(f"https://api.openalex.org/works/doi:{quote(doi, safe='')}")
        if r.status_code != 200:
            return None
        try:
            return parse_openalex(r.json())
        except (ValueError, AttributeError):
            return None


async def open_access(doi: str) -> dict | None:
    """Find a free copy. OpenAlex is the default because it works out of the
    box; Unpaywall is consulted only as a fallback, and only if the user
    supplied a contact email (its API rejects requests without one)."""
    info = await openalex(doi)
    if info and info.get("pdf_url"):
        return info
    if config.EMAIL:
        alt = await unpaywall(doi)
        if alt and alt.get("pdf_url"):
            return alt
    return info


async def unpaywall(doi: str) -> dict | None:
    """Secondary source. Returns None unless a contact email is configured."""
    if not config.EMAIL:
        return None
    async with _client() as c:
        r = await c.get(
            f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
            params={"email": config.EMAIL},
        )
        if r.status_code != 200:
            return None
        try:
            return parse_unpaywall(r.json())
        except (ValueError, AttributeError):
            return None
