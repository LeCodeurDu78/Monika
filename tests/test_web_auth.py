"""The page's optional token: every route and the live channel refuse a request without it."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from web.hub import Hub
from web.server import COOKIE, create_app

TOKEN = "un-jeton-de-test"


async def snapshot() -> dict:
    return {"ok": True}


def client(token: str = TOKEN) -> TestClient:
    return TestClient(create_app(Hub(), snapshot, token=token))


def test_every_route_needs_the_token() -> None:
    with client() as page:
        assert page.get("/api/status").status_code == 401
        assert page.get("/api/status", headers={"X-Monika-Token": "faux"}).status_code == 401
        assert page.get("/").status_code == 401


def test_header_or_query_opens_the_page() -> None:
    with client() as page:
        assert page.get("/api/status", headers={"X-Monika-Token": TOKEN}).status_code == 200
        assert page.get(f"/api/status?token={TOKEN}").status_code == 200


def test_the_link_sets_a_cookie_and_leaves_the_address_bar() -> None:
    with client() as page:
        opened = page.get(f"/?token={TOKEN}", follow_redirects=False)

        assert opened.status_code == 303
        assert opened.headers["location"] == "/"
        assert COOKIE in opened.cookies
        assert page.get("/api/status").status_code == 200  # the cookie alone is enough


def test_the_cookie_is_secure_behind_https() -> None:
    app = create_app(Hub(), snapshot, token=TOKEN)
    with TestClient(app, base_url="https://testserver") as page:
        opened = page.get(f"/?token={TOKEN}", follow_redirects=False)

        assert "secure" in opened.headers["set-cookie"].lower()


def test_the_cookie_still_works_in_plain_http() -> None:
    with client() as page:  # base_url http://testserver
        opened = page.get(f"/?token={TOKEN}", follow_redirects=False)

        assert "secure" not in opened.headers["set-cookie"].lower()
        assert page.get("/api/status").status_code == 200


def test_live_channel_refuses_a_missing_token() -> None:
    with client() as page:
        with pytest.raises(WebSocketDisconnect):
            with page.websocket_connect("/ws"):
                pass


def test_without_a_token_the_page_stays_open_locally() -> None:
    with client(token="") as page:
        assert page.get("/api/status").status_code == 200
