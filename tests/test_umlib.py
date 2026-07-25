from pathlib import Path

import httpx
import pytest

from umlib_mcp import browser, config, fetch, oa


def test_proxied_prefix():
    assert (
        config.proxied("https://www.jstor.org/stable/24265183")
        == "https://proxy.lib.umich.edu/login?url=https://www.jstor.org/stable/24265183"
    )


def test_is_web_url():
    assert config.is_web_url("https://x.org/a")
    assert config.is_web_url("http://x.org/a")
    assert not config.is_web_url("file:///etc/passwd")
    assert not config.is_web_url("javascript:alert(1)")
    assert not config.is_web_url("data:text/html,x")


def test_extract_doi():
    assert (
        oa.extract_doi("see https://doi.org/10.1145/3411764.3445642.")
        == "10.1145/3411764.3445642"
    )
    assert oa.extract_doi("DOI: 10.1038/nature12373, fig 2") == "10.1038/nature12373"
    assert oa.extract_doi("no doi here") is None


def test_extract_doi_brackets_and_parens():
    assert oa.extract_doi("[10.1038/nature12373].") == "10.1038/nature12373"
    assert (
        oa.extract_doi("(see 10.1016/0167-2789(92)90242-F)")
        == "10.1016/0167-2789(92)90242-F"
    )


def test_is_proxied_url():
    assert browser.is_proxied_url("https://www-jstor-org.proxy.lib.umich.edu/stable/1")
    assert browser.is_proxied_url("https://proxy.lib.umich.edu/menu")
    assert not browser.is_proxied_url(
        "https://proxy.lib.umich.edu/login?url=https://x.org"
    )
    assert not browser.is_proxied_url("https://weblogin.umich.edu/?cosign")
    assert not browser.is_proxied_url("https://www.jstor.org/stable/1")


def test_prepare_candidates_relative_resolves_and_proxies():
    page = "https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/a"
    out = fetch.prepare_candidates(["/doi/pdf/10.1/a"], page, "dl.acm.org")
    assert out == ["https://dl-acm-org.proxy.lib.umich.edu/doi/pdf/10.1/a"]


def test_prepare_candidates_remaps_bare_publisher_host():
    page = "https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/a"
    out = fetch.prepare_candidates(
        ["https://dl.acm.org/doi/pdf/10.1/a"], page, "dl.acm.org"
    )
    assert out == [
        "https://proxy.lib.umich.edu/login?url=https://dl.acm.org/doi/pdf/10.1/a"
    ]


def test_prepare_candidates_drops_offproxy_and_junk():
    page = "https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/a"
    out = fetch.prepare_candidates(
        ["https://evil.example/x.pdf", "javascript:void(0)", None, "mailto:a@b"],
        page,
        "dl.acm.org",
    )
    assert out == []


def test_prepare_candidates_keeps_epdf_rewrite():
    page = "https://x-org.proxy.lib.umich.edu/p"
    out = fetch.prepare_candidates(["/doi/epdf/10.1/a"], page, "x.org")
    assert out == ["https://x-org.proxy.lib.umich.edu/doi/pdf/10.1/a"]


def test_slugify_filename():
    assert (
        fetch.slugify_filename("Moderation Matters: A Study!", 2024, "10.1/x")
        == "moderation-matters-a-study-2024.pdf"
    )
    assert (
        fetch.slugify_filename(None, None, "10.1145/3411764.3445642")
        == "10.1145_3411764.3445642.pdf"
    )


def test_save_pdf_unique(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path)
    p1 = fetch.save_pdf(b"%PDF-1.7 x", "a.pdf")
    p2 = fetch.save_pdf(b"%PDF-1.7 y", "a.pdf")
    assert p1.name == "a.pdf" and p2.name == "a-2.pdf"
    assert p1.read_bytes().startswith(b"%PDF")


def test_save_pdf_sanitizes_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path)
    p = fetch.save_pdf(b"%PDF-1.7", "../../evil.pdf")
    assert p.parent == tmp_path and p.name == "evil.pdf"


def test_save_pdf_case_insensitive_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path)
    p = fetch.save_pdf(b"%PDF-1.7", "Report.PDF")
    assert p.name == "Report.pdf"


def test_save_pdf_nul_byte_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path)
    p = fetch.save_pdf(b"%PDF-1.7", "a\x00b.pdf")
    assert p.parent == tmp_path and p.suffix == ".pdf"


def test_oversized_helper(monkeypatch):
    monkeypatch.setattr(config, "MAX_PDF_BYTES", 1000)
    assert fetch._oversized("2000")
    assert not fetch._oversized("500")
    assert not fetch._oversized(None)
    assert not fetch._oversized("not-a-number")


def test_logout_refuses_dangerous_profile_dirs(tmp_path, monkeypatch):
    """The marker file alone is not a safe guard: ensure_profile_dir() writes
    it wherever UMLIB_PROFILE_DIR points, so $HOME must be rejected outright."""
    from pathlib import Path

    from umlib_mcp import auth

    home = tmp_path / "home"
    (home / ".umlib" / "profile").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    # marker present and inside home, yet each of these must still be refused
    for dangerous in (home, home.parent):
        monkeypatch.setattr(config, "PROFILE_DIR", dangerous)
        monkeypatch.setattr(config, "PROFILE_MARKER", dangerous / ".umlib-managed")
        config.PROFILE_MARKER.touch(exist_ok=True)
        assert auth.clear_session()["cleared"] is False
        assert dangerous.exists(), f"{dangerous} was deleted"

    # the filesystem root, without touching it
    monkeypatch.setattr(config, "PROFILE_DIR", Path(home.anchor))
    monkeypatch.setattr(config, "PROFILE_MARKER", Path(home.anchor) / ".umlib-managed")
    assert auth.clear_session()["cleared"] is False

    # a real profile under home is still removable
    good = home / ".umlib" / "profile"
    monkeypatch.setattr(config, "PROFILE_DIR", good)
    monkeypatch.setattr(config, "PROFILE_MARKER", good / ".umlib-managed")
    config.PROFILE_MARKER.touch()
    assert auth.clear_session()["cleared"] is True
    assert not good.exists()


def test_release_file_lock_is_idempotent():
    from umlib_mcp import browser

    browser._release_file_lock(9999)  # never held: must be a no-op, not a close


def test_extract_doi_strips_url_query_and_fragment():
    assert (
        oa.extract_doi("https://dl.acm.org/doi/10.1145/3359252?ref=nav#abstract")
        == "10.1145/3359252"
    )
    assert oa.extract_doi("10.1080/1369118X.2021.1899282?needAccess=true") == (
        "10.1080/1369118X.2021.1899282"
    )


def test_slugify_falls_back_to_doi_for_non_latin_titles():
    # a title that slugifies to nothing must not produce a nameless file
    assert (
        fetch.slugify_filename("日本語のタイトル", 2024, "10.1/x") == "10.1_x-2024.pdf"
    )
    assert fetch.slugify_filename("", None, None) == "article.pdf"


def test_setting_precedence_env_then_file_then_default(monkeypatch):
    monkeypatch.setattr(config, "_FILE", {"max_fetches_per_hour": 30})
    monkeypatch.delenv("UMLIB_MAX_FETCHES_PER_HOUR", raising=False)
    assert (
        config.setting("max_fetches_per_hour", 60, int) == 30
    )  # file wins over default

    monkeypatch.setenv("UMLIB_MAX_FETCHES_PER_HOUR", "12")
    assert config.setting("max_fetches_per_hour", 60, int) == 12  # env wins over file

    monkeypatch.setenv("UMLIB_MAX_FETCHES_PER_HOUR", "banana")
    assert (
        config.setting("max_fetches_per_hour", 60, int) == 30
    )  # bad env falls to file

    monkeypatch.setattr(config, "_FILE", {"max_fetches_per_hour": "also-bad"})
    assert config.setting("max_fetches_per_hour", 60, int) == 60  # both bad -> default


def test_malformed_config_file_does_not_crash(tmp_path, monkeypatch):
    bad = tmp_path / "config.toml"
    bad.write_text("this is not [valid toml")
    monkeypatch.setattr(config, "CONFIG_FILE", bad)
    assert config._load_file() == {}


def test_email_must_be_well_formed_before_reaching_a_header(monkeypatch):
    import importlib

    for raw, expect in [
        ("you@umich.edu", "you@umich.edu"),
        (" you@umich.edu ", "you@umich.edu"),
        ("not an email", ""),  # would break every lookup via the User-Agent
        ("you@umich.edu\nX-Injected: 1", ""),
        ("", ""),
    ]:
        monkeypatch.setenv("UMLIB_EMAIL", raw)
        reloaded = importlib.reload(config)
        assert expect == reloaded.EMAIL, raw
    monkeypatch.delenv("UMLIB_EMAIL", raising=False)
    importlib.reload(config)


def test_untrusted_text_is_clipped_and_stripped():
    from umlib_mcp.server import _safe

    assert _safe("a" * 5000).endswith("...")
    assert len(_safe("a" * 5000)) <= 303
    assert "\n" not in _safe("line1\nline2")
    assert "\x00" not in _safe("nul\x00byte")


def test_save_pdf_is_bounded_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DOWNLOAD_DIR", tmp_path)
    # a very long name must still be writable rather than failing after the
    # fetch has already been paid for
    p = fetch.save_pdf(b"%PDF-1.7", "x" * 500 + ".pdf")
    assert p.exists() and len(p.name) <= 130


def test_hostile_page_cannot_amplify_candidate_requests():
    """The JS cap runs in the publisher's page, where Set and Array.slice are
    page-owned, so a hostile page can return a list of any length or type.
    The real limit has to be enforced here, in Python."""
    page = "https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/a"
    flood = [f"/doi/pdf/10.1/{i}" for i in range(5000)]
    assert (
        len(fetch.prepare_candidates(flood, page, "dl.acm.org")) == fetch.MAX_CANDIDATES
    )

    # a page can also return something that is not a list of strings at all
    assert fetch.prepare_candidates(12345, page, "dl.acm.org") == []
    assert fetch.prepare_candidates("not-a-list", page, "dl.acm.org") == []
    assert fetch.prepare_candidates([{"href": "x"}, None, 7], page, "dl.acm.org") == []


def test_candidates_stay_on_this_articles_host():
    """A link to a different licensed publisher is still 'proxied', but
    following it would pull another publisher's content under the licence."""
    page = "https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/a"
    out = fetch.prepare_candidates(
        ["https://www-sciencedirect-com.proxy.lib.umich.edu/x.pdf"], page, "dl.acm.org"
    )
    assert out == []


def test_unproxy_host_decodes_hyphenated_publishers(monkeypatch):
    monkeypatch.setattr(config, "REWRITE_HOST", "proxy.lib.umich.edu")
    # EZproxy encodes a literal hyphen as "--"
    assert (
        fetch._unproxy_host("https://link--springer-com.proxy.lib.umich.edu/a")
        == "link-springer.com"
    )


def test_private_hosts_are_refused_for_open_access_downloads():
    assert not fetch._is_public_host("http://localhost:8080/x.pdf")
    assert not fetch._is_public_host("http://127.0.0.1/x.pdf")
    assert not fetch._is_public_host("http://169.254.169.254/latest/meta-data/")
    assert not fetch._is_public_host("http://[::1]/x.pdf")
    assert not fetch._is_public_host("http://nonexistent.invalid/x.pdf")


def test_oclc_hosted_schools_get_the_right_rewrite_host():
    assert (
        config._default_rewrite_host("login.proxy.lib.example.idm.oclc.org")
        == "proxy.lib.example.idm.oclc.org"
    )
    assert config._default_rewrite_host("proxy.lib.umich.edu") == "proxy.lib.umich.edu"


def test_plausible_match_rejects_unrelated_titles():
    from umlib_mcp.server import _plausible_match as ok

    assert ok(
        "Does Transparency in Moderation Really Matter Jhaver",
        "Does Transparency in Moderation Really Matter?",
    )
    assert ok("attention is all you need", "Attention Is All You Need")
    assert not ok("zzqx nonexistent paper title 99177", "Grant Report: Something Else")
    assert not ok("anything", None)
    # short acronyms are real search terms and must still count
    assert ok("AI moderation", "AI Moderation at Scale")
    # a query with nothing meaningful in it cannot be judged, so it passes
    assert ok("the and of for", "A Completely Different Paper About Frogs")


def test_proxied_is_not_applied_twice():
    once = config.proxied("https://dl.acm.org/doi/10.1/x")
    assert config.proxied(once) == once
    # an already-rewritten host is also left alone
    rewritten = "https://www-jstor-org.proxy.lib.umich.edu/stable/1"
    assert config.proxied(rewritten) == rewritten


def test_is_web_url_is_case_insensitive():
    assert config.is_web_url("HTTPS://dl.acm.org/x")
    assert config.is_web_url("Http://x.org")
    assert not config.is_web_url("FILE:///etc/passwd")


def test_is_proxied_url_honours_a_separate_rewrite_host(monkeypatch):
    """Some institutions serve the login page and the rewritten hosts from
    different domains."""
    monkeypatch.setattr(config, "PROXY_HOST", "login.lib.example.edu")
    monkeypatch.setattr(config, "REWRITE_HOST", "ezp.example.edu")
    assert browser.is_proxied_url("https://www-jstor-org.ezp.example.edu/stable/1")
    assert not browser.is_proxied_url("https://login.lib.example.edu/login?url=x")
    assert not browser.is_proxied_url("https://www.jstor.org/stable/1")


def test_unproxy_host_recovers_the_publisher(monkeypatch):
    monkeypatch.setattr(config, "REWRITE_HOST", "proxy.lib.umich.edu")
    assert (
        fetch._unproxy_host("https://dl-acm-org.proxy.lib.umich.edu/doi/10.1/x")
        == "dl.acm.org"
    )
    assert fetch._unproxy_host("https://dl.acm.org/doi/10.1/x") == ""


def test_shipped_defaults(monkeypatch):
    """With nothing configured, the shipped courtesy limits apply."""
    monkeypatch.setattr(config, "_FILE", {})
    monkeypatch.delenv("UMLIB_MAX_FETCHES_PER_HOUR", raising=False)
    monkeypatch.delenv("UMLIB_MIN_FETCH_INTERVAL_S", raising=False)
    assert config.setting("max_fetches_per_hour", 60, int) == 60
    assert config.setting("min_fetch_interval_s", 5.0, float) == 5.0


def test_ratelimit_refunds_the_callers_own_slot(monkeypatch):
    """Two fetches can be in flight, so refund must return the caller's slot
    rather than whichever was taken most recently."""
    import asyncio

    from umlib_mcp import ratelimit

    monkeypatch.setattr(ratelimit, "_slots", {})
    monkeypatch.setattr(ratelimit, "_last_fetch", 0.0)
    monkeypatch.setattr(config, "MIN_FETCH_INTERVAL_S", 0.0)

    async def run():
        before = ratelimit.remaining_this_hour()
        a = await ratelimit.acquire()
        b = await ratelimit.acquire()
        assert ratelimit.remaining_this_hour() == before - 2
        ratelimit.refund(a)  # the older slot, not the newest
        assert ratelimit.remaining_this_hour() == before - 1
        ratelimit.refund(a)  # refunding twice must not credit a second slot
        assert ratelimit.remaining_this_hour() == before - 1
        ratelimit.refund(b)
        assert ratelimit.remaining_this_hour() == before
        ratelimit.refund(None)  # a never-charged fetch is a no-op

    asyncio.run(run())


def test_parse_openalex():
    j = {
        "open_access": {"is_oa": True, "oa_status": "diamond"},
        "best_oa_location": {"pdf_url": "https://ojs/article/download/1"},
    }
    parsed = oa.parse_openalex(j)
    assert parsed["pdf_url"] == "https://ojs/article/download/1"
    assert parsed["is_oa"] and parsed["oa_status"] == "diamond"
    # a paywalled record has no pdf even when a landing page exists
    closed = oa.parse_openalex(
        {"open_access": {"is_oa": False}, "best_oa_location": {}}
    )
    assert closed["pdf_url"] is None and closed["is_oa"] is False


def test_parse_unpaywall_prefers_pdf_url():
    j = {
        "is_oa": True,
        "oa_status": "green",
        "best_oa_location": {
            "url": "https://x/landing",
            "url_for_pdf": "https://x/f.pdf",
        },
    }
    parsed = oa.parse_unpaywall(j)
    assert parsed["pdf_url"] == "https://x/f.pdf"
    assert oa.parse_unpaywall({"is_oa": False})["pdf_url"] is None


def test_parse_crossref():
    m = {
        "DOI": "10.1/x",
        "title": ["A Title"],
        "issued": {"date-parts": [[2023, 5]]},
        "container-title": ["CHI"],
        "author": [{"family": "Lee"}, {"family": "Kim"}],
        "URL": "https://doi.org/10.1/x",
        "resource": {"primary": {"URL": "https://dl.acm.org/doi/10.1/x"}},
    }
    parsed = oa.parse_crossref(m)
    assert parsed["publisher_url"] == "https://dl.acm.org/doi/10.1/x"
    assert parsed["year"] == 2023 and parsed["authors"] == ["Lee", "Kim"]


def test_session_state_roundtrip(tmp_path, monkeypatch):
    """EZproxy/Okta session cookies live only in memory, so the state file is
    what keeps a signed-in session usable by the next browser launch."""
    import asyncio

    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "session-state.json")

    class StubContext:
        def __init__(self, cookies=None):
            self._cookies = cookies or []
            self.added = []

        async def storage_state(self):
            return {"cookies": self._cookies}

        async def add_cookies(self, cookies):
            self.added.extend(cookies)

    session_cookie = [
        {"name": "ezproxy", "value": "x", "domain": "p.umich.edu", "path": "/"}
    ]
    asyncio.run(browser._save_state(StubContext(session_cookie)))
    assert config.STATE_FILE.exists()

    fresh = StubContext()
    asyncio.run(browser._restore_state(fresh))
    assert [c["name"] for c in fresh.added] == ["ezproxy"]

    # an empty jar must never clobber a good saved session
    asyncio.run(browser._save_state(StubContext([])))
    assert "ezproxy" in config.STATE_FILE.read_text()


def test_message_guards_non_json_and_missing_key():
    assert oa._message(httpx.Response(500)) is None
    assert oa._message(httpx.Response(200, content=b"<html>not json")) is None
    assert oa._message(httpx.Response(200, json={"no_message": 1})) is None
    assert oa._message(httpx.Response(200, json={"message": {"DOI": "10.1/x"}})) == {
        "DOI": "10.1/x"
    }


# --- regressions found by the post-fix audit ---------------------------------


def test_oclc_sign_in_page_is_not_licensed_content(monkeypatch):
    """At an OCLC school the sign-in host is itself a subdomain of the rewrite
    host, so testing the subdomain branch first made the proxy's own login page
    look like licensed content: auth_status reported a session nobody had."""
    monkeypatch.setattr(config, "PROXY_HOST", "login.example.idm.oclc.org")
    monkeypatch.setattr(config, "REWRITE_HOST", "example.idm.oclc.org")
    assert not browser.is_proxied_url(
        "https://login.example.idm.oclc.org/login?url=https://www.jstor.org/"
    )
    assert not browser.is_proxied_url("https://login.example.idm.oclc.org/logout")
    # a genuinely rewritten article on the same proxy still counts
    assert browser.is_proxied_url("https://www-jstor-org.example.idm.oclc.org/stable/1")


def test_profile_lock_lives_outside_the_deletable_profile_dir(tmp_path, monkeypatch):
    """logout rmtrees PROFILE_DIR. flock binds to the inode, so a lock inside
    that tree let a waiter keep the deleted inode while the next process
    acquired the recreated file - two browsers on one profile."""
    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "profile")
    assert not browser._lock_path().is_relative_to(config.PROFILE_DIR)

    fd = browser._acquire_file_lock(1.0)
    try:
        # taking the lock must not conjure up a profile directory: logout would
        # then leave one behind, and auth_status would probe instead of saying
        # "no session yet"
        assert not config.PROFILE_DIR.exists()
    finally:
        browser._release_file_lock(fd)


def test_cancelled_pre_yield_teardown_releases_the_profile_lock(tmp_path, monkeypatch):
    """A CancelledError is a BaseException, so suppress(Exception) around the
    shielded close let it skip the release and strand the flock for the life of
    the process - wedging every later fetch, login and logout on the machine."""
    import asyncio
    import contextlib

    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "profile")

    class SlowCtx:
        async def close(self):
            await asyncio.sleep(5)  # so the cancellation lands inside the close

    async def fake_launch(headless):
        return SlowCtx()

    async def failing_restore(ctx):
        raise RuntimeError("pre-yield failure")

    monkeypatch.setattr(browser, "_launch", fake_launch)
    monkeypatch.setattr(browser, "_restore_state", failing_restore)

    async def scenario():
        async def use():
            async with browser.session(headless=True):
                pass

        task = asyncio.create_task(use())
        await asyncio.sleep(0.2)
        task.cancel()
        with contextlib.suppress(BaseException):
            await task
        assert not browser._held_locks
        # the real proof is that the profile can be locked again
        fd = await asyncio.to_thread(browser._acquire_file_lock, 2.0)
        browser._release_file_lock(fd)

    asyncio.run(scenario())


def test_targeted_pdf_selectors_run_before_the_catch_all():
    """The candidate list is capped, so a page carrying a pile of ordinary .pdf
    links (author guides, supplements) starved out the one publisher-specific
    link that was actually the full text."""
    js = fetch.PDF_CANDIDATE_JS
    catch_all = js.index('a[href*=".pdf"]')
    for specific in ("/stamp/stamp.jsp", "/pdfdirect", "downloadpdf", "/pdfft"):
        assert js.index(specific) < catch_all, specific


def test_open_access_download_accepts_a_gzipped_pdf():
    """aiter_bytes yields decompressed bytes while Content-Length describes the
    compressed transfer, so comparing the two rejected every gzipped PDF and
    fell through to the licensed path for a paper that was free."""
    import asyncio
    import gzip
    import http.server
    import socketserver
    import threading

    body = b"%PDF-1.4\n" + b"x" * 20000 + b"\n%%EOF\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path == "/gzip.pdf":
                payload = gzip.compress(body)
                self.send_response(200)
                self.send_header("Content-Encoding", "gzip")
            elif self.path == "/truncated.pdf":
                payload = body[:5000]
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))  # a lie
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                payload = body
                self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        # the SSRF guard rejects loopback by design; this test is about the
        # content-length logic that runs after it
        original = fetch._is_public_host
        fetch._is_public_host = lambda url: True
        try:
            get = fetch.download_open_access
            assert asyncio.run(get(f"http://127.0.0.1:{port}/plain.pdf")) == body
            assert asyncio.run(get(f"http://127.0.0.1:{port}/gzip.pdf")) == body
            # a genuinely short read must still be caught
            assert asyncio.run(get(f"http://127.0.0.1:{port}/truncated.pdf")) is None
        finally:
            fetch._is_public_host = original
    finally:
        srv.shutdown()


def test_zero_fetches_per_hour_switches_licensed_fetching_off(monkeypatch):
    """It used to report zero remaining and then let the fetch through."""
    import asyncio

    from umlib_mcp import ratelimit

    monkeypatch.setattr(config, "MAX_FETCHES_PER_HOUR", 0)
    monkeypatch.setattr(ratelimit, "_slots", {})
    assert ratelimit.remaining_this_hour() == 0
    with pytest.raises(ratelimit.RateLimited):
        asyncio.run(ratelimit.acquire())


def test_bad_settings_are_reported_instead_of_killing_the_server(monkeypatch):
    """Every one of these used to raise at import, so the server never started
    and the user saw nothing but their agent failing to connect."""
    import importlib

    for var, value, check in [
        # urlparse raises "Invalid IPv6 URL" on an unbalanced bracket
        ("UMLIB_PROXY_BASE", "https://[::1/login?url=", lambda m: not m.PROXY_HOST),
        # expanduser raises RuntimeError on an unknown ~user
        ("UMLIB_DOWNLOAD_DIR", "~nosuchuser12345/Papers", lambda m: m.DOWNLOAD_DIR),
        # the session cookie must never travel in the clear
        (
            "UMLIB_PROXY_BASE",
            "http://proxy.example.edu/login?url=",
            lambda m: not m.PROXY_HOST,
        ),
    ]:
        monkeypatch.setenv(var, value)
        reloaded = importlib.reload(config)
        assert reloaded.CONFIG_ERROR, f"{var}={value} should be reported"
        assert check(reloaded), f"{var}={value}"
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)


def test_resolver_base_can_actually_be_switched_off(monkeypatch):
    """setting() skips empty values as "unset", so an empty resolver_base fell
    back to U-M's resolver and non-U-M users got U-M links in every result."""
    import importlib

    monkeypatch.setenv("UMLIB_RESOLVER_BASE", "")
    reloaded = importlib.reload(config)
    assert reloaded.RESOLVER_BASE == ""
    assert reloaded.mgetit_url("10.1/x") == ""
    monkeypatch.delenv("UMLIB_RESOLVER_BASE", raising=False)
    importlib.reload(config)


def test_a_toml_bool_does_not_become_a_number(monkeypatch):
    """int(True) is 1, so `max_fetches_per_hour = true` silently became a cap
    of one fetch an hour."""
    monkeypatch.setattr(config, "_FILE", {"max_fetches_per_hour": True})
    monkeypatch.delenv("UMLIB_MAX_FETCHES_PER_HOUR", raising=False)
    assert config.setting("max_fetches_per_hour", 60, int) == 60


def test_settings_under_a_section_header_are_reported(tmp_path, monkeypatch):
    """A [umlib] table is the common TOML mistake, and every setting in it was
    silently ignored."""
    import importlib

    cfg = tmp_path / "config.toml"
    cfg.write_text("[umlib]\nmax_fetches_per_hour = 5\n")
    monkeypatch.setenv("UMLIB_CONFIG", str(cfg))
    reloaded = importlib.reload(config)
    assert reloaded.MAX_FETCHES_PER_HOUR == 60
    assert "section" in reloaded.CONFIG_ERROR
    monkeypatch.delenv("UMLIB_CONFIG", raising=False)
    importlib.reload(config)


def test_a_missing_doi_is_not_reported_as_a_network_problem():
    """crossref_work returned None for both a 404 and an unreachable service,
    so a typo'd DOI told the user to check their connection."""
    import asyncio

    async def fake_get_404(self, url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))

    async def fake_get_boom(self, url, **kw):
        raise httpx.ConnectError("down")

    original = httpx.AsyncClient.get
    try:
        httpx.AsyncClient.get = fake_get_404
        assert asyncio.run(oa.crossref_work("10.1/nope")) == {}  # answered: no such DOI
        httpx.AsyncClient.get = fake_get_boom
        assert asyncio.run(oa.crossref_work("10.1/nope")) is None  # unreachable
    finally:
        httpx.AsyncClient.get = original


# --- regressions found in the bcee43a fix batch itself -----------------------


def test_resolve_distinguishes_a_missing_doi_from_an_unreachable_service():
    """crossref_work started returning {} for a 404, but resolve still guarded
    with `is None`, so one mistyped character produced status "ok" carrying no
    metadata at all."""
    import asyncio

    from umlib_mcp import server

    async def missing(doi):
        return {}

    async def unreachable(doi):
        return None

    original = oa.crossref_work
    try:
        oa.crossref_work = missing
        out = asyncio.run(server.resolve("10.1145/3411764.3445642x"))
        assert out["status"] == "error" and out["code"] == "doi_not_found"

        oa.crossref_work = unreachable
        out = asyncio.run(server.resolve("10.1145/3411764.3445642x"))
        assert out["status"] == "error" and out["code"] == "lookup_unavailable"
    finally:
        oa.crossref_work = original


def test_login_refuses_to_open_a_window_for_a_rejected_proxy_base(monkeypatch):
    """login was the only proxy tool without the config gate, so the user
    completed a full password + MFA sign-in against a base config had already
    refused, then got told it timed out."""
    import asyncio

    from umlib_mcp import server

    monkeypatch.setattr(config, "PROXY_HOST", "")
    monkeypatch.setattr(config, "CONFIG_ERROR", "proxy_base must start with https://")
    out = asyncio.run(server.login())
    assert out.get("code") == "config_error"
    assert not out.get("started")


def test_a_sign_in_page_is_never_licensed_on_any_proxy_host(monkeypatch):
    """Binding the auth-path exclusion to PROXY_HOST alone left the SSO
    provider counted as licensed content whenever it sits on another subdomain
    of the rewrite host."""
    monkeypatch.setattr(config, "PROXY_HOST", "ezproxy.school.edu")
    monkeypatch.setattr(config, "REWRITE_HOST", "school.edu")
    assert not browser.is_proxied_url(
        "https://weblogin.school.edu/idp/profile/SAML2/Redirect/SSO"
    )
    assert not browser.is_proxied_url("https://ezproxy.school.edu/login?url=x")
    # a genuinely rewritten article on the same apex still counts
    assert browser.is_proxied_url("https://www-jstor-org.school.edu/stable/1")


def test_lock_is_shared_between_two_spellings_of_one_profile_dir(tmp_path):
    """The lock was keyed on the profile path STRING, so on a case-insensitive
    filesystem two servers could hold 'different' locks on one directory."""
    real = tmp_path / "Profile"
    real.mkdir()
    lower = tmp_path / "profile"
    from unittest.mock import patch

    with patch.object(config, "PROFILE_DIR", real):
        a = browser._lock_path()
    with patch.object(config, "PROFILE_DIR", lower):
        b = browser._lock_path()
    # same parent directory and a name derived only from the profile's own
    # name, so the two resolve to one inode wherever the filesystem folds case
    assert a.parent == b.parent
    assert a.name.lower() == b.name.lower()


def test_lock_survives_a_profile_path_that_is_not_valid_utf8(tmp_path, monkeypatch):
    """Hashing the path with strict utf-8 made every browser call fail on a
    latin-1 filename, which is legal on Linux."""
    weird = tmp_path / "weird\udcff" / "profile"
    monkeypatch.setattr(config, "PROFILE_DIR", weird)
    assert browser._lock_path()  # must not raise UnicodeEncodeError


def test_unusable_umlib_config_does_not_stop_the_server_starting(monkeypatch):
    """The one path in the module that never got the no-raise guard."""
    import importlib

    monkeypatch.setenv("UMLIB_CONFIG", "~nosuchuser12345/umlib.toml")
    reloaded = importlib.reload(config)  # used to raise RuntimeError at import
    assert "UMLIB_CONFIG" in reloaded.CONFIG_ERROR
    monkeypatch.delenv("UMLIB_CONFIG", raising=False)
    importlib.reload(config)


def test_candidate_js_is_bounded_and_keeps_what_it_collected():
    """Removing the page-side cap let a page serialise 200k hrefs into this
    process; the outer catch also threw away everything already collected."""
    js = fetch.PDF_CANDIDATE_JS
    assert "slice(0, 200)" in js
    # `out` must be declared outside the try, or the catch cannot reach it
    assert js.index("const out = []") < js.index("try {")
    assert js.count("slice(0, 200)") >= 2  # the success path and the catch


def test_logout_with_no_session_is_not_reported_as_a_refusal(tmp_path, monkeypatch):
    """A fresh install and a second logout both hit the ownership guard, which
    reads like a safety problem rather than 'there was nothing to do'."""
    from umlib_mcp import auth

    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "never-created")
    out = auth.clear_session()
    assert out["cleared"] is True
    assert "refusing" not in out["message"]


# --- regressions found in the 6f17545 batch ----------------------------------


def test_rewritten_publisher_paths_are_not_mistaken_for_sign_in_pages(monkeypatch):
    """EBSCOhost serves its permalinks from /login.aspx. Applying the auth-path
    exclusion to rewritten publisher hosts sent users to sign in when they were
    already looking at the licensed article."""
    monkeypatch.setattr(config, "PROXY_HOST", "proxy.lib.umich.edu")
    monkeypatch.setattr(config, "REWRITE_HOST", "proxy.lib.umich.edu")
    assert browser.is_proxied_url(
        "https://search-ebscohost-com.proxy.lib.umich.edu/login.aspx?direct=true&db=aph"
    )
    assert browser.is_proxied_url(
        "https://www-jstor-org.proxy.lib.umich.edu/stable/24265183"
    )
    # the proxy's own sign-in and logout pages still do not count
    assert not browser.is_proxied_url("https://proxy.lib.umich.edu/login?url=x")
    assert not browser.is_proxied_url("https://proxy.lib.umich.edu/logout")


def test_sso_handoff_is_still_excluded_on_a_rewrite_apex(monkeypatch):
    """The identity provider sits on a plain label, a rewritten site on a
    hyphen-encoded one, so the two stay distinguishable."""
    monkeypatch.setattr(config, "PROXY_HOST", "ezproxy.school.edu")
    monkeypatch.setattr(config, "REWRITE_HOST", "school.edu")
    assert not browser.is_proxied_url(
        "https://weblogin.school.edu/idp/profile/SAML2/Redirect/SSO"
    )
    assert browser.is_proxied_url("https://www-jstor-org.school.edu/stable/1")
    assert browser.is_proxied_url("https://search-ebscohost-com.school.edu/login.aspx")


def test_resolve_falls_back_to_open_access_for_a_datacite_doi():
    """Crossref 404s every arXiv, Zenodo, OSF and figshare DOI. Returning
    doi_not_found for those told the user to check a DOI that was fine, and
    skipped the open-access lookup that would have found the paper."""
    import asyncio

    from umlib_mcp import server

    async def no_crossref_record(doi):
        return {}

    async def has_free_copy(doi):
        return {"is_oa": True, "pdf_url": "https://arxiv.org/pdf/2005.14165"}

    async def no_free_copy(doi):
        return None

    orig_work, orig_oa = oa.crossref_work, oa.open_access
    try:
        oa.crossref_work = no_crossref_record
        oa.open_access = has_free_copy
        out = asyncio.run(server.resolve("10.48550/arXiv.2005.14165"))
        assert out["status"] == "ok"
        assert out["open_access"]["pdf_url"].endswith("2005.14165")

        # a DOI that genuinely does not exist anywhere is still an error
        oa.open_access = no_free_copy
        out = asyncio.run(server.resolve("10.1145/nope"))
        assert out["code"] == "doi_not_found"
    finally:
        oa.crossref_work, oa.open_access = orig_work, orig_oa


def test_logout_works_when_home_is_a_symlink(tmp_path, monkeypatch):
    """PROFILE_DIR is resolved but Path.home() was not, so is_relative_to was
    false for everyone whose home is a symlink and logout refused forever."""
    from umlib_mcp import auth

    real = tmp_path / "realhome"
    (real / ".umlib" / "profile").mkdir(parents=True)
    link = tmp_path / "linkhome"
    link.symlink_to(real)
    profile = (real / ".umlib" / "profile").resolve()
    monkeypatch.setattr(config, "PROFILE_DIR", profile)
    monkeypatch.setattr(config, "PROFILE_MARKER", profile / ".umlib-managed")
    config.PROFILE_MARKER.touch()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: link))

    out = auth.clear_session()
    assert out["cleared"] is True, out["message"]
    assert not profile.exists()


def test_logout_clears_the_cached_login_result(tmp_path, monkeypatch):
    """The no-profile early return skipped the reset, so auth_status kept
    reporting a sign-in that logout had just said was gone."""
    from umlib_mcp import auth

    monkeypatch.setattr(config, "PROFILE_DIR", tmp_path / "absent")
    auth._last_result = {"authenticated": True, "message": "login complete"}
    out = auth.clear_session()
    assert out["cleared"] is True
    assert auth.last_login_result() is None
    # and it names the path, since a profile may exist somewhere else
    assert str(tmp_path / "absent") in out["message"]


def test_login_tells_the_user_there_is_no_confirmation(monkeypatch):
    """The window closing is the only signal the user gets. Without saying so,
    a successful sign-in looks exactly like a failed one."""
    import asyncio

    from umlib_mcp import auth

    monkeypatch.setattr(auth, "_login_task", None)
    monkeypatch.setattr(auth, "_no_display", lambda: "")

    async def go():
        async def never():
            await asyncio.sleep(60)

        monkeypatch.setattr(auth, "_run_login", never)
        return auth.start_login()

    out = asyncio.run(go())
    assert out["started"]
    assert "nothing to wait for" in out["tell_user"]
    # and the assistant is told not to stop here
    assert "auth_status" in out["next_step"]


def test_login_warns_about_the_first_run_download(monkeypatch):
    import asyncio

    from umlib_mcp import auth

    monkeypatch.setattr(auth, "_login_task", None)
    monkeypatch.setattr(auth, "_no_display", lambda: "")

    async def never():
        await asyncio.sleep(60)

    monkeypatch.setattr(auth, "_run_login", never)

    monkeypatch.setattr(browser, "browser_ready", lambda: False)
    assert "downloads" in asyncio.run(_start(auth))["tell_user"]
    monkeypatch.setattr(browser, "browser_ready", lambda: True)
    assert "downloads" not in asyncio.run(_start(auth))["tell_user"]


async def _start(auth):
    auth._login_task = None
    return auth.start_login()


def test_browser_ready_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "nope"))
    assert browser.browser_ready() is False
    (tmp_path / "yes").mkdir()
    (tmp_path / "yes" / "chromium-1234").mkdir()
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "yes"))
    assert browser.browser_ready() is True


def test_download_progress_is_parsed_and_reported(monkeypatch):
    """The first run pulls ~150MB. Without progress the sign-in window simply
    does not appear for a minute, with nothing said."""
    import asyncio

    class FakeStdout:
        def __init__(self, chunks):
            self._chunks = list(chunks)

        async def read(self, _n):
            return self._chunks.pop(0) if self._chunks else b""

    class FakeProc:
        returncode = 0

        def __init__(self, chunks):
            self.stdout = FakeStdout(chunks)

        async def wait(self):
            return 0

    # what playwright actually emits: a redrawn bar, percentages repeating
    chunks = [
        b"Downloading Chromium 141.0 (playwright build v1228)\n",
        b"|                    |   0% of 143.5 MiB",
        b"|####                |  20% of 143.5 MiB",
        b"|####                |  20% of 143.5 MiB",  # redraw, must not re-report
        b"|##########          |  55% of 143.5 MiB",
        b"|####################| 100% of 143.5 MiB\n",
    ]

    async def fake_exec(*a, **kw):
        return FakeProc(chunks)

    monkeypatch.setattr(browser, "browser_ready", lambda: False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    seen = []

    async def on_progress(pct, note):
        seen.append(pct)

    asyncio.run(browser.ensure_chromium(on_progress))
    assert seen == [0, 20, 55, 100]  # monotonic, redraw collapsed
    assert not browser._install_lock.locked()  # released even on the happy path


def test_download_failure_surfaces_the_reason(monkeypatch):
    import asyncio

    class FakeStdout:
        def __init__(self):
            self._done = False

        async def read(self, _n):
            if self._done:
                return b""
            self._done = True
            return b"Error: connection refused by cdn.playwright.dev"

    class FakeProc:
        returncode = 1

        def __init__(self):
            self.stdout = FakeStdout()

        async def wait(self):
            return 1

    async def fake_exec(*a, **kw):
        return FakeProc()

    monkeypatch.setattr(browser, "browser_ready", lambda: False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(RuntimeError, match="connection refused"):
        asyncio.run(browser.ensure_chromium(None))
    assert not browser._install_lock.locked()  # and the lock is not stranded


def test_pdf_accepted_when_a_publisher_redirects_to_its_cdn():
    """Silverchair (OUP and a long tail of society journals) authenticates via
    the proxy and then hands the bytes off to a signed CDN URL on another host.
    Requiring the redirect chain to END on the proxy reported no_pdf_found on a
    PDF we had already been given."""
    import asyncio

    class Resp:
        def __init__(self, url, body):
            self.url, self.ok, self._b = url, True, body
            self.headers = {"content-type": "application/pdf"}

        async def body(self):
            return self._b

    class Req:
        def __init__(self, url, body):
            self._r = Resp(url, body)

        async def get(self, url, **kw):
            return self._r

    class Ctx:
        def __init__(self, url, body):
            self.request = Req(url, body)

    pdf = b"%PDF-1.7" + b"x" * 500
    proxied = "https://academic-oup-com.proxy.lib.umich.edu/jcmc/article-pdf/1.pdf"

    # ends on a public CDN off the proxy: accepted
    cdn = Ctx("https://watermark02.silverchair.com/x.pdf?token=abc", pdf)
    assert asyncio.run(fetch._pdf_from_request(cdn, proxied)) == pdf

    # ends back on the proxy: still accepted
    onprox = Ctx(proxied, pdf)
    assert asyncio.run(fetch._pdf_from_request(onprox, proxied)) == pdf

    # ends somewhere internal: still refused, so a hostile redirect cannot
    # turn this into an SSRF
    for internal in (
        "http://127.0.0.1/secret.pdf",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8080/x.pdf",
    ):
        assert asyncio.run(fetch._pdf_from_request(Ctx(internal, pdf), proxied)) is None

    # and a non-PDF body is refused wherever it came from
    assert (
        asyncio.run(fetch._pdf_from_request(Ctx(proxied, b"<html>nope"), proxied))
        is None
    )
