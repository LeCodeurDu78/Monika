"""Le pont helper : presets Hermes/nanoMuse, client HTTP, outils MCP et refus."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from bridges.helper_bridge import (
    HelperClient,
    HelperConfigError,
    HelperError,
    build_server,
    config_from_env,
)


class FakeHelper(BaseHTTPRequestHandler):
    """Un helper générique : /ask, /status/{id}, /answer, /stop, /health."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # Silence the test output
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text: str, code: int = 200) -> None:
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append(
            (self.command, self.path, self.headers.get("Authorization"), body)
        )
        return body

    def do_GET(self) -> None:
        self._record()
        if self.path == "/health":
            self._send({"ok": True, "version": "1.2"})
        elif self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif self.path == "/status/t1":
            sequence = self.server.status_sequence
            self._send(sequence.pop(0) if len(sequence) > 1 else sequence[0])
        else:
            self._send({"error": "inconnu"}, 404)

    def do_POST(self) -> None:
        body = self._record()
        if self.path == "/ask":
            if body.get("prompt") == "boom":
                self._send({"error": "échec volontaire"}, 500)
            elif body.get("prompt") == "texte":
                self._send_text("réponse brute")
            else:
                self._send({"id": "t1", "state": "running"})
        elif self.path == "/answer":
            self.server.answer = body
            self._send({"id": "t1", "state": "running"})
        elif self.path == "/stop":
            self._send({"id": "t1", "state": "stopped"})
        else:
            self._send({"error": "inconnu"}, 404)


class FakeHermes(BaseHTTPRequestHandler):
    """Les routes de l'API Runs de Hermes Agent (sans le vrai agent)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append(
            (self.command, self.path, self.headers.get("Authorization"), body)
        )
        return body

    def do_GET(self) -> None:
        self._record()
        if self.path == "/health":
            self._send({"status": "ok"})
        elif self.path == "/v1/runs/run_1":
            self._send({"run_id": "run_1", "status": "running"})
        elif self.path == "/v1/runs/run_2":
            if self.server.approved:
                self._send(
                    {
                        "run_id": "run_2",
                        "status": "completed",
                        "output": "Approuvé.",
                    }
                )
            else:
                self._send(
                    {
                        "object": "hermes.run",
                        "run_id": "run_2",
                        "status": "waiting_for_approval",
                        "pending_controls": [
                            {
                                "prompt_id": "p1",
                                "execution_generation": 2,
                                "kind": "approval",
                                "summary": "Supprimer le fichier ?",
                                "choices": ["once", "deny"],
                            }
                        ],
                    }
                )
        elif self.path == "/v1/runs/run_3":
            self._send(
                {
                    "object": "hermes.run",
                    "run_id": "run_3",
                    "status": "waiting_for_clarification",
                    "pending_controls": [
                        {
                            "prompt_id": "p2",
                            "execution_generation": 1,
                            "kind": "clarify",
                            "question": "Quel fichier ?",
                        }
                    ],
                }
            )
        else:
            self._send({"error": "inconnu"}, 404)

    def do_POST(self) -> None:
        body = self._record()
        if self.path == "/v1/runs":
            run_id = {
                "approval": "run_2",
                "clarify": "run_3",
            }.get(body.get("input"), "run_1")
            self._send({"run_id": run_id, "status": "started"})
        elif self.path == "/v1/runs/run_2/approval":
            self.server.approved = True
            self._send(
                {"run_id": "run_2", "status": "completed", "output": "Approuvé."}
            )
        elif self.path == "/v1/runs/run_3/clarify":
            self._send(
                {"run_id": "run_3", "status": "completed", "output": "README.md"}
            )
        elif self.path == "/v1/runs/run_1/stop":
            self._send({"status": "stopping"})
        else:
            self._send({"error": "inconnu"}, 404)


class FakeNanoMuse(BaseHTTPRequestHandler):
    """Les routes locales de nanoMuse : fils, événements, approbations."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        self.server.calls.append(
            (self.command, self.path, self.headers.get("Authorization"), body)
        )
        return body

    def do_GET(self) -> None:
        self._record()
        if self.path == "/api/health":
            self._send({"ok": True, "version": "1.0", "auth": True})
        elif self.path == "/api/threads/t_1/events":
            self._send(
                {
                    "thread": {"id": "t_1", "busy": False},
                    "events": [
                        {"id": "u_1", "type": "user", "text": "fais"},
                        {"id": "a_1", "type": "assistant", "text": "C'est fait."},
                    ],
                }
            )
        elif self.path == "/api/threads/t_2/events":
            approval = {
                "id": "ap_1",
                "type": "approval",
                "status": "approved" if self.server.approved else "pending",
                "summary": "Supprimer le fichier ?",
            }
            events = [
                {"id": "u_2", "type": "user", "text": "supprime"},
                approval,
            ]
            if self.server.approved:
                events.append(
                    {"id": "a_2", "type": "assistant", "text": "Approuvé."}
                )
            self._send(
                {
                    "thread": {"id": "t_2", "busy": not self.server.approved},
                    "events": events,
                }
            )
        elif self.path == "/api/threads/t_3/events":
            events = [
                {"id": "u_3", "type": "user", "text": "lis un fichier"},
                {
                    "id": "q_1",
                    "type": "question",
                    "status": "answered" if self.server.asked else "pending",
                    "text": "Quel fichier ?",
                },
            ]
            if self.server.asked:
                events.append(
                    {"id": "a_3", "type": "assistant", "text": "README.md"}
                )
            self._send(
                {
                    "thread": {"id": "t_3", "busy": not self.server.asked},
                    "events": events,
                }
            )
        else:
            self._send({"error": "inconnu"}, 404)

    def do_POST(self) -> None:
        body = self._record()
        if self.path == "/api/threads":
            self._send({"id": "t_1", "title": body.get("title", ""), "busy": True})
        elif self.path == "/api/threads/t_1/send":
            self._send(
                {
                    "event": {"id": "u_1", "type": "user"},
                    "thread": {"id": "t_1", "busy": True},
                }
            )
        elif self.path == "/api/threads/t_3/send":
            self.server.asked = True
            self._send(
                {
                    "event": {"id": "u_4", "type": "user"},
                    "thread": {"id": "t_3", "busy": True},
                }
            )
        elif self.path == "/api/approvals/ap_1":
            self.server.approved = True
            self._send({"ok": True})
        elif self.path == "/api/threads/t_1/stop":
            self._send({"ok": True})
        else:
            self._send({"error": "inconnu"}, 404)


@contextlib.contextmanager
def served(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    server.calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def helper():
    with served(FakeHelper) as pair:
        url, server = pair
        server.status_sequence = [{"id": "t1", "state": "running"}]
        server.answer = None
        yield url, server


@pytest.fixture
def hermes():
    with served(FakeHermes) as pair:
        url, server = pair
        server.approved = False
        yield url, server


@pytest.fixture
def nanomuse():
    with served(FakeNanoMuse) as pair:
        url, server = pair
        server.approved = False
        server.asked = False
        yield url, server


def config(url: str, **values):
    return config_from_env(
        {"HELPER_URL": url, "HELPER_TOKEN": "secret", "HELPER_POLL_S": "0.01", **values}
    )


def test_the_round_trip_reaches_every_route_and_sends_the_token(helper) -> None:
    url, server = helper
    client = HelperClient(config(url))

    assert client.health() == {"ok": True, "helper": "helper", "detail": "1.2"}

    task = client.ask("fais quelque chose", "contexte")
    assert task == {"id": "t1", "state": "running"}

    server.status_sequence = [{"id": "t1", "state": "done", "result": "fini"}]
    assert client.status("t1")["result"] == "fini"

    assert client.answer("t1", "oui")["state"] == "running"
    assert server.answer == {"id": "t1", "answer": "oui"}

    assert client.stop("t1")["state"] == "stopped"
    assert all(call[2] == "Bearer secret" for call in server.calls)
    assert [call[0] for call in server.calls] == ["GET", "POST", "GET", "POST", "POST"]


def test_wait_stops_on_a_terminal_state(helper) -> None:
    url, server = helper
    server.status_sequence = [
        {"id": "t1", "state": "running"},
        {"id": "t1", "state": "done", "result": "42"},
    ]

    assert HelperClient(config(url)).ask("calcule", wait_s=1)["result"] == "42"


def test_a_plain_text_answer_becomes_a_result(helper) -> None:
    url, _ = helper

    task = HelperClient(config(url)).ask("texte")

    assert task == {"state": "done", "result": "réponse brute"}


def test_an_http_error_is_reported_with_its_body(helper) -> None:
    url, _ = helper

    with pytest.raises(HelperError) as caught:
        HelperClient(config(url)).ask("boom")

    assert "HTTP 500" in str(caught.value)
    assert "échec volontaire" in str(caught.value)


def test_a_redirect_is_refused_instead_of_leaking_the_token(helper) -> None:
    url, _ = helper
    client = HelperClient(
        config_from_env({"HELPER_URL": url, "HELPER_HEALTH_PATH": "/redirect"})
    )

    result = client.health()

    assert result["ok"] is False
    assert "HTTP 302" in result["detail"]


def test_missing_url_and_status_placeholder_are_refused() -> None:
    with pytest.raises(HelperConfigError, match="HELPER_URL"):
        config_from_env({})

    with pytest.raises(HelperConfigError, match=r"\{id\}"):
        config_from_env(
            {"HELPER_URL": "http://127.0.0.1:9", "HELPER_STATUS_PATH": "/status"}
        )


def test_a_bad_task_id_never_reaches_the_url(helper) -> None:
    url, server = helper

    with pytest.raises(HelperError, match="Identifiant"):
        HelperClient(config(url)).status("../autre")

    assert server.calls == []


def test_the_server_exposes_the_five_tools(helper) -> None:
    url, _ = helper
    server = build_server(HelperClient(config(url)))

    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {"ask", "status", "answer", "stop", "health"}


def test_monika_can_connect_over_stdio_and_call_a_tool(helper) -> None:
    url, _ = helper
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "bridges.helper_bridge"],
        env={
            **os.environ,
            "HELPER_URL": url,
            "HELPER_NAME": "Hermes",
            "HELPER_TOKEN": "secret",
        },
        cwd=os.getcwd(),
    )

    async def run() -> tuple[set[str], dict]:
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("health", {})
                text = "".join(
                    block.text
                    for block in result.content
                    if getattr(block, "type", "") == "text"
                )
                return {tool.name for tool in tools.tools}, json.loads(text)

    names, health = asyncio.run(asyncio.wait_for(run(), 20))

    assert names == {"ask", "status", "answer", "stop", "health"}
    assert health == {"ok": True, "helper": "Hermes", "detail": "1.2"}


def test_presets_use_the_real_helper_routes() -> None:
    hermes = config_from_env(
        {"HELPER_URL": "http://127.0.0.1:8642", "HELPER_KIND": "hermes"}
    )
    assert hermes.kind == "hermes"
    assert hermes.ask_path == "/v1/runs"
    assert hermes.status_path == "/v1/runs/{id}"
    assert hermes.approval_path == "/v1/runs/{id}/approval"
    assert hermes.clarify_path == "/v1/runs/{id}/clarify"
    assert hermes.stop_path == "/v1/runs/{id}/stop"
    assert hermes.health_path == "/health"

    nanomuse = config_from_env(
        {"HELPER_URL": "http://127.0.0.1:8765", "HELPER_KIND": "nanomuse"}
    )
    assert nanomuse.kind == "nanomuse"
    assert nanomuse.ask_path == "/api/threads"
    assert nanomuse.send_path == "/api/threads/{id}/send"
    assert nanomuse.status_path == "/api/threads/{id}/events"
    assert nanomuse.approvals_path == "/api/approvals/{id}"
    assert nanomuse.stop_path == "/api/threads/{id}/stop"
    assert nanomuse.health_path == "/api/health"


def test_an_unknown_kind_or_answer_mode_is_refused() -> None:
    with pytest.raises(HelperConfigError, match="HELPER_KIND"):
        config_from_env(
            {"HELPER_URL": "http://127.0.0.1:9", "HELPER_KIND": "autre"}
        )

    with pytest.raises(HelperConfigError, match="HELPER_ANSWER_MODE"):
        config_from_env(
            {
                "HELPER_URL": "http://127.0.0.1:9",
                "HELPER_KIND": "hermes",
                "HELPER_ANSWER_MODE": "devine",
            }
        )

    with pytest.raises(HelperConfigError, match=r"\{id\}"):
        config_from_env(
            {
                "HELPER_URL": "http://127.0.0.1:9",
                "HELPER_KIND": "hermes",
                "HELPER_STOP_PATH": "/stop",
            }
        )


def test_hermes_maps_runs_approvals_and_clarifications(hermes) -> None:
    url, server = hermes
    client = HelperClient(config(url, HELPER_KIND="hermes", HELPER_NAME="Hermes"))

    task = client.ask("fais quelque chose", "reste simple")
    assert task == {"id": "run_1", "state": "running"}
    assert server.calls[0][3] == {
        "input": "fais quelque chose",
        "instructions": "reste simple",
    }

    assert client.status("run_1")["state"] == "running"

    assert client.ask("approval")["id"] == "run_2"
    waiting = client.status("run_2")
    assert waiting["state"] == "waiting_for_approval"
    assert "Supprimer" in waiting["question"]

    approved = client.answer("run_2", "oui")
    assert approved == {"id": "run_2", "state": "done", "result": "Approuvé."}
    assert server.calls[-1][3] == {
        "request_id": "p1",
        "execution_generation": 2,
        "choice": "once",
    }

    assert client.ask("clarify")["id"] == "run_3"
    waiting = client.status("run_3")
    assert waiting["state"] == "waiting_for_answer"
    assert waiting["question"] == "Quel fichier ?"

    clarified = client.answer("run_3", "README.md")
    assert clarified == {"id": "run_3", "state": "done", "result": "README.md"}
    assert server.calls[-1][3] == {
        "request_id": "p2",
        "execution_generation": 1,
        "answer": "README.md",
    }

    assert client.stop("run_1") == {"id": "run_1", "state": "stopping"}
    assert client.health() == {"ok": True, "helper": "Hermes", "detail": "ok"}
    assert all(call[2] == "Bearer secret" for call in server.calls)


def test_nanomuse_creates_a_thread_and_answers_it(nanomuse) -> None:
    url, server = nanomuse
    client = HelperClient(
        config(url, HELPER_KIND="nanomuse", HELPER_NAME="nanoMuse")
    )

    task = client.ask("fais quelque chose")
    assert task["id"] == "t_1"
    assert server.calls[0][1] == "/api/threads"
    assert server.calls[0][3] == {"title": "fais quelque chose"}
    assert server.calls[1][1] == "/api/threads/t_1/send"
    assert server.calls[1][3] == {"text": "fais quelque chose"}

    assert client.status("t_1") == {
        "id": "t_1",
        "state": "done",
        "result": "C'est fait.",
    }

    approved = client.answer("t_2", "oui")
    assert approved == {"id": "t_2", "state": "done", "result": "Approuvé."}
    approval_call = next(
        call
        for call in reversed(server.calls)
        if call[0] == "POST" and call[1] == "/api/approvals/ap_1"
    )
    assert approval_call[3] == {
        "approved": True,
        "scope": "once",
        "reason": "",
    }

    clarified = client.answer("t_3", "README.md")
    assert clarified == {"id": "t_3", "state": "done", "result": "README.md"}
    answer_call = next(
        call
        for call in reversed(server.calls)
        if call[0] == "POST" and call[1] == "/api/threads/t_3/send"
    )
    assert answer_call[3] == {"text": "README.md"}

    assert client.stop("t_1") == {"id": "t_1", "state": "stopping"}
    assert client.health() == {"ok": True, "helper": "nanoMuse", "detail": "1.0"}
    assert all(call[2] == "Bearer secret" for call in server.calls)
