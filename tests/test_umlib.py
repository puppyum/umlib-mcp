import httpx

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


def test_shipped_defaults(monkeypatch):
    """With nothing configured, the shipped courtesy limits apply."""
    monkeypatch.setattr(config, "_FILE", {})
    monkeypatch.delenv("UMLIB_MAX_FETCHES_PER_HOUR", raising=False)
    monkeypatch.delenv("UMLIB_MIN_FETCH_INTERVAL_S", raising=False)
    assert config.setting("max_fetches_per_hour", 60, int) == 60
    assert config.setting("min_fetch_interval_s", 5.0, float) == 5.0


def test_ratelimit_refund_restores_a_slot(monkeypatch):
    from umlib_mcp import ratelimit

    monkeypatch.setattr(ratelimit, "_times", type(ratelimit._times)())
    before = ratelimit.remaining_this_hour()
    ratelimit._times.append(__import__("time").monotonic())
    assert ratelimit.remaining_this_hour() == before - 1
    ratelimit.refund()
    assert ratelimit.remaining_this_hour() == before
    ratelimit.refund()  # refunding an empty ledger must not go negative or raise
    assert ratelimit.remaining_this_hour() == before


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
