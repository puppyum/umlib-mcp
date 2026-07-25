import asyncio
import contextlib
import ipaddress
import re
import socket
from pathlib import Path
from urllib.parse import urljoin

import httpx

from . import browser, config, ratelimit
from .auth import NeedsLogin


class NoPdfFound(Exception):
    def __init__(self, page_url: str):
        self.page_url = page_url
        super().__init__(f"no downloadable PDF located on {page_url}")


class HostNotProxied(Exception):
    """EZproxy bounced to the raw publisher host: the site is not in the
    proxy's database, so the library licenses it another way or not at all."""


# Collect PDF candidates from a rendered publisher page. citation_pdf_url
# covers most publishers; the selectors cover the stragglers (ScienceDirect
# pdfft, Wiley/T&F/SAGE/ACM doi/pdf, Springer content/pdf).
PDF_CANDIDATE_JS = """() => {
  const out = [];
  const push = (u) => { if (u && typeof u === 'string') out.push(u); };
  const meta = document.querySelector('meta[name="citation_pdf_url"]');
  push(meta && meta.content);
  document.querySelectorAll('link[type="application/pdf"]').forEach((l) => push(l.href));
  const sels = [
    'a[href*="/pdfft"]',
    'a[href*="/doi/pdf"]',
    'a[href*="/doi/epdf"]',
    'a[href*="/content/pdf/"]',
    'a[href$=".pdf"]',
  ];
  for (const s of sels) document.querySelectorAll(s).forEach((a) => push(a.href));
  return [...new Set(out)].slice(0, 8);
}"""


def _unproxy_host(proxied_page_url: str) -> str:
    """EZproxy rewrites www.jstor.org to www-jstor-org.proxy.lib.umich.edu, so
    recover the real publisher host from the page we actually landed on."""
    host = browser.host_of(proxied_page_url)
    suffix = "." + config.REWRITE_HOST
    if not host.endswith(suffix):
        return ""
    # EZproxy encodes a dot as "-" and a literal hyphen as "--", so
    # link-springer.com arrives as link--springer-com. Decode in that order.
    label = host[: -len(suffix)]
    return label.replace("--", "\x00").replace("-", ".").replace("\x00", "-")


MAX_CANDIDATES = 8


def prepare_candidates(urls, page_url: str, publisher_host: str) -> list[str]:
    """Turn raw PDF hrefs harvested from an untrusted publisher DOM into a
    safe fetch list. Relative links resolve against the proxied page; links
    already on the proxy pass through; links on the bare publisher host are
    re-proxied so the session cookie applies; anything else (an arbitrary
    attacker host) is dropped so we never send credentialed requests off-proxy.
    """
    # The JS runs in the publisher's own page, where Set and Array.slice are
    # page-owned, so its slice(0, 8) is not a cap we control: a hostile page
    # can return a list of any length or any type. Everything below is
    # enforced here, in Python, where the page cannot reach it.
    if not isinstance(urls, list):
        return []
    out: list[str] = []
    for u in urls:
        if len(out) >= MAX_CANDIDATES:
            break
        if (
            not isinstance(u, str)
            or not u
            or u.startswith(("javascript:", "mailto:", "#"))
        ):
            continue
        u = urljoin(page_url, u).replace("/doi/epdf/", "/doi/pdf/")
        if not config.is_web_url(u):
            continue
        # only this article's own host: a link to a *different* licensed
        # publisher would still be "proxied", and following it would let one
        # publisher's page pull content from another under the user's licence
        if browser.host_of(u) == browser.host_of(page_url):
            candidate = u
        elif browser.host_of(u) == publisher_host:
            candidate = config.proxied(u)  # raw publisher host -> back through proxy
        else:
            continue  # anywhere else: don't fetch it with our session cookies
        if candidate not in out:
            out.append(candidate)
    return out


def slugify_filename(title: str | None, year, doi: str | None) -> str:
    slug = ""
    if title:
        # a title in a non-Latin script slugifies to nothing, so fall through
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60].rstrip("-")
    if not slug and doi:
        slug = re.sub(r"[^a-z0-9.]+", "_", doi.lower())
    if not slug:
        slug = "article"
    return f"{slug}-{year}.pdf" if year else f"{slug}.pdf"


def save_pdf(data: bytes, filename: str) -> Path:
    config.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    # filename can arrive as tool input: basename + allowlist, no traversal,
    # and a NUL byte must not crash the call
    try:
        base = Path(filename).name
    except ValueError:
        base = "article"
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-.") or "article"
    path = config.DOWNLOAD_DIR / f"{base}.pdf"
    n = 1
    while path.exists():
        n += 1
        path = config.DOWNLOAD_DIR / f"{base}-{n}.pdf"
    path.write_bytes(data)
    return path


def _oversized(content_length: str | None) -> bool:
    return bool(
        content_length
        and content_length.isdigit()
        and int(content_length) > config.MAX_PDF_BYTES
    )


def _is_public_host(url: str) -> bool:
    """A metadata service is a third party; never let a URL it hands back
    point the fetcher at localhost, the link-local metadata service, or
    anything else on the private network."""
    host = browser.host_of(url)
    if not host or host == "localhost" or host.endswith(".localhost"):
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


async def download_open_access(url: str) -> bytes | None:
    if not config.is_web_url(url):
        return None
    if not await asyncio.to_thread(_is_public_host, url):
        return None
    try:
        async with (
            httpx.AsyncClient(
                headers={"User-Agent": config.USER_AGENT},
                timeout=60,
                follow_redirects=True,
                max_redirects=5,
            ) as c,
            c.stream("GET", url) as r,
        ):
            if r.status_code != 200 or _oversized(r.headers.get("content-length")):
                return None
            declared = r.headers.get("content-length")
            chunks, total = [], 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > config.MAX_PDF_BYTES:
                    return None
                chunks.append(chunk)
        # a connection cut mid-download would otherwise be saved as a
        # complete, silently corrupt PDF
        if declared and declared.isdigit() and total != int(declared):
            return None
        body = b"".join(chunks)
        return body if body.startswith(b"%PDF") else None
    except (httpx.HTTPError, ValueError, UnicodeError, OSError):
        # httpx raises non-HTTPError types for malformed URLs and bad hosts
        return None
    except TimeoutError:
        return None


async def _pdf_from_request(ctx, url: str) -> bytes | None:
    # EZproxy always bounces a /login?url= link at least once, so redirects
    # have to be followed; what matters is that we refuse the body if the
    # chain ended up somewhere off the proxy.
    r = await ctx.request.get(url, timeout=60_000)
    if not r.ok or not browser.is_proxied_url(r.url):
        return None
    if _oversized(r.headers.get("content-length")):
        return None
    body = await r.body()
    if len(body) > config.MAX_PDF_BYTES or not body.startswith(b"%PDF"):
        return None
    return body


async def fetch_licensed(publisher_url: str) -> tuple[bytes, str]:
    """Fetch one PDF through the user's authenticated EZproxy session.

    The cap is charged before we touch the proxy, so a capped user generates no
    publisher traffic at all, and refunded when the attempt never reached
    licensed content (no session, or the host isn't proxied).
    """
    proxied_url = config.proxied(publisher_url)
    publisher_host = browser.host_of(publisher_url)
    await ratelimit.acquire()
    try:
        return await _fetch_via_proxy(proxied_url, publisher_host)
    except (NeedsLogin, HostNotProxied):
        raise  # already refunded at the point of detection
    except NoPdfFound:
        # the proxy authenticated us and the publisher page was fetched, so
        # this consumed a licensed fetch whether or not a PDF came back
        raise
    except BaseException:
        ratelimit.refund()  # browser never got us to licensed content
        raise


async def _fetch_via_proxy(proxied_url: str, publisher_host: str):
    async with browser.session(headless=True) as ctx:
        page = await ctx.new_page()
        downloads = []
        page.on("download", lambda d: downloads.append(d))
        aborted = False
        try:
            await page.goto(proxied_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception as e:
            # a direct-PDF URL makes the browser start a download instead of
            # rendering; that only happens once we're through the proxy
            if "Download is starting" not in str(e) and "ERR_ABORTED" not in str(e):
                raise  # fetch_licensed refunds on the way out
            aborted = True
            # the download event races the goto rejection; give it a moment
            for _ in range(30):
                if downloads:
                    break
                await asyncio.sleep(0.1)
        if downloads:
            data = await _read_download(downloads[0])
            if data.startswith(b"%PDF"):
                return data, downloads[0].url
            raise NoPdfFound(proxied_url)

        # busy pages never go idle; the DOM is enough
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=10_000)

        if aborted:
            # the navigation was replaced by a download that never arrived;
            # page.url is about:blank, which says nothing about the session,
            # so do not misreport this as "you are signed out"
            raise NoPdfFound(proxied_url)

        if not browser.is_proxied_url(page.url):
            ratelimit.refund()  # never got past the proxy: not a licensed fetch
            if browser.host_of(page.url) == publisher_host:
                # bounced straight back to the publisher: not in the proxy's
                # database. (A redirector like doi.org is the ambiguous case:
                # we land elsewhere and report needs_login, which carries the
                # landed host so the user can see where they ended up.)
                raise HostNotProxied(page.url)
            raise NeedsLogin(page.url)

        # after the proxy has rewritten the URL, the real publisher host is
        # whatever it landed on, not whatever was passed in
        landed_host = _unproxy_host(page.url) or publisher_host
        candidates = prepare_candidates(
            await page.evaluate(PDF_CANDIDATE_JS), page.url, landed_host
        )
        for candidate in candidates:
            with contextlib.suppress(Exception):
                body = await _pdf_from_request(ctx, candidate)
                if body:
                    return body, candidate
        raise NoPdfFound(page.url)


async def _read_download(download) -> bytes:
    with contextlib.suppress(Exception):
        path = await download.path()
        if path:
            data = Path(path).read_bytes()
            return data if len(data) <= config.MAX_PDF_BYTES else b""
    return b""
