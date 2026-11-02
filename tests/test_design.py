"""Design rules that are easy to regress: self-hosted fonts, no template tells, one animation."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.result_page import render_result_page

STATIC = Path(main.__file__).parent / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
ROW = {
    "id": 1, "decision": "hold", "reasons_json": '["Looks wrong"]', "findings_json": '{"rules": [], "ai": []}',
    "actions": [{"action": "held", "detail": "Money held"}], "shop_key": "techmart", "product_id": "tm-case-01",
    "agent_claimed_amount": "18.00", "agent_reason": "Fits", "order_id": "O1", "authorization_id": "A1",
    "paypal_amount": "18.00", "paypal_currency": "USD", "payee_merchant_id": "2LEDKUAGXMU34", "budget": "20.00",
}


@pytest.fixture
def client():
    return TestClient(main.app)


def pages(client):
    return {
        "/": client.get("/").text,
        "/lab": client.get("/lab").text,
        "/security": client.get("/security").text,
        "result": render_result_page(ROW),
    }


def test_fonts_are_self_hosted_and_csp_is_unchanged(client):
    for weight in (400, 500, 600, 700):
        assert f"/static/fonts/ibm-plex-sans-latin-{weight}-normal.woff2" in CSS
    for weight in (400, 500):
        assert f"/static/fonts/ibm-plex-mono-latin-{weight}-normal.woff2" in CSS
    for url in re.findall(r'url\("([^"]+)"\)', CSS):
        assert url.startswith("/static/fonts/"), url
        assert client.get(url).status_code == 200, url
    assert "http" not in CSS.replace("http-equiv", "")
    assert main.CONTENT_SECURITY_POLICY == (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    )
    assert (STATIC / "fonts" / "IBMPlexSans-OFL.txt").exists() and (STATIC / "fonts" / "IBMPlexMono-OFL.txt").exists()


def test_no_all_caps_labels_or_arrow_suffixes(client):
    assert "uppercase" not in CSS
    for name, html in pages(client).items():
        visible = re.sub(r"<[^>]+>", " ", re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S))
        assert "→" not in visible and "&rarr;" not in html and "&larr;" not in html, name
        assert " · " not in visible and "&middot;" not in html, name
        for word in ("ALLOW", "BLOCK", "HOLD", "PASS", "FAIL", "FLAGGED", "UNTRUSTED", "TRUSTED"):
            assert not re.search(rf"\b{word}\b", visible), (name, word)


def test_only_the_result_page_animates_and_motion_can_be_reduced(client):
    keyframes = re.findall(r"@keyframes\s+([\w-]+)", CSS)
    assert sorted(keyframes) == ["stage-fill", "stage-in"]
    assert "transition" not in CSS
    assert "@media (prefers-reduced-motion: reduce)" in CSS
    html = pages(client)
    assert 'class="progress progress-animate"' in html["result"]
    for name in ("/", "/lab", "/security"):
        assert "progress-animate" not in html[name], name


def test_every_page_has_visible_focus_and_the_shared_nav(client):
    assert ":focus-visible" in CSS
    for name, html in pages(client).items():
        assert 'class="site-nav"' in html, name


LOGO = '<img class="brand-logo" src="/static/logo.svg" alt="" width="16" height="16">'
FAVICON = '<link rel="icon" type="image/svg+xml" href="/static/logo.svg">'


def all_pages(client):
    from app.result_page import render_still_checking_page

    return {**pages(client), "still-checking": render_still_checking_page("ORD-1")}


def test_logo_and_favicon_on_every_page(client):
    for name, html in all_pages(client).items():
        assert html.count(LOGO) == 1, name
        head = html.split("</head>", 1)[0]
        assert FAVICON in head, name
        assert 'brand-mark' not in html, name  # the old square's class, still styled by stale cached CSS


def test_pages_have_no_inline_scripts(client):
    for name, html in all_pages(client).items():
        for tag in re.findall(r"<script\b[^>]*>", html):
            assert re.fullmatch(r'<script src="/static/[\w.]+\.js" defer>', tag), (name, tag)
        assert not re.search(r"<script\b[^>]*>\s*[^<\s]", html), name
        assert not re.search(r"\son\w+\s*=", html), name


def test_logo_svg_is_a_plain_static_shield(client):
    resp = client.get("/static/logo.svg")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/svg+xml")
    svg = resp.text
    assert 'viewBox="0 0 20 20"' in svg
    assert 'stroke="#4A9EDB"' in svg and 'stroke-width="2"' in svg and 'fill="none"' in svg
    assert "stroke-dasharray" in svg  # the trust-boundary line
    for banned in ("<script", "<text", "Gradient", "<image", "href", "on", "<style"):
        if banned == "on":
            assert not re.search(r"\son\w+\s*=", svg)
        else:
            assert banned not in svg, banned



def test_logo_is_16px_and_centred_with_the_title():
    assert ".brand-logo { width: 16px; height: 16px; flex: none; display: block; }" in CSS
    assert re.search(r"\.brand \{[^}]*display: flex;[^}]*align-items: center;", CSS)



def test_no_rule_for_the_old_square_class():
    rules = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    assert ".brand-mark" not in rules
    assert ".brand-logo {" in rules


@pytest.mark.parametrize("path", ["/", "/lab", "/security", "/static/style.css", "/static/logo.svg", "/static/app.js"])
def test_pages_and_static_files_are_revalidated(client, path):
    resp = client.get(path)
    assert resp.headers["cache-control"] == "no-cache"


def test_unchanged_static_file_revalidates_with_304(client):
    first = client.get("/static/style.css")
    etag = first.headers["etag"]
    again = client.get("/static/style.css", headers={"If-None-Match": etag})
    assert again.status_code == 304
