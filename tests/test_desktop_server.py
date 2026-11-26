"""
Browser-mode server tests.

`slr-ui --browser` exposes the app's API on a local port. That API can open
files and write to a review, so the rules that keep it local-only are tested
directly: no token, no call; a foreign Host header is refused; static files
never leave the web folder; private methods are not reachable.
"""
import http.client
import json
from pathlib import Path

import pytest

pytest.importorskip("rich")

from slr_desktop.api import Api
from slr_desktop.server import make_server
from slr_desktop.service import ReviewService

TOKEN = "test-token"


@pytest.fixture
def server(tmp_path, monkeypatch):
    import threading
    monkeypatch.setenv("SLR_UI_SETTINGS", str(tmp_path / "settings.json"))
    httpd, url = make_server(Api(ReviewService(tmp_path / "ws")), token=TOKEN)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1], url
    httpd.shutdown()
    httpd.server_close()


def request(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request(method, path, body=json.dumps(body) if body is not None else None,
                 headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


def api(port, name, args=(), token=TOKEN, **headers):
    hdrs = {"Content-Type": "application/json", **headers}
    if token is not None:
        hdrs["X-SLR-Token"] = token
    status, data = request(port, "POST", f"/api/{name}", {"args": list(args)}, hdrs)
    return status, json.loads(data)


def test_url_binds_to_loopback_and_carries_the_token(server):
    port, url = server
    assert url == f"http://127.0.0.1:{port}/index.html?t={TOKEN}"


def test_serves_the_page_and_its_scripts(server):
    port, _ = server
    status, body = request(port, "GET", f"/index.html?t={TOKEN}")
    assert status == 200 and b"SLR Pipeline UI" in body
    for name in ("app.css", "core.js", "main.js"):
        assert request(port, "GET", "/" + name)[0] == 200


def test_api_call_with_token_works(server):
    port, _ = server
    status, out = api(port, "list_runs")
    assert status == 200 and out == {"ok": True, "data": []}


@pytest.mark.parametrize("token", [None, "", "wrong"])
def test_api_call_without_the_token_is_refused(server, token):
    port, _ = server
    status, out = api(port, "list_runs", token=token)
    assert status == 403 and out["ok"] is False


def test_foreign_host_header_is_refused(server):
    port, _ = server
    status, out = api(port, "list_runs", Host="evil.example")
    assert status == 403 and out["ok"] is False
    assert request(port, "GET", "/index.html", headers={"Host": "evil.example"})[0] == 403


@pytest.mark.parametrize("path", ["/../service.py", "/..%2Fservice.py", "/../../../LICENSE.txt",
                                  "/nope.js"])
def test_static_files_stay_inside_the_web_folder(server, path):
    port, _ = server
    assert request(port, "GET", path)[0] == 404


@pytest.mark.parametrize("name", ["_bind", "_svc", "__init__", "no_such_call"])
def test_private_and_unknown_calls_are_not_reachable(server, name):
    port, _ = server
    status, out = api(port, name)
    assert status == 404 and out["ok"] is False


def test_errors_come_back_as_messages_not_tracebacks(server):
    port, _ = server
    status, out = api(port, "get_run", ["does-not-exist"])
    assert status == 200 and out["ok"] is False and "No review called" in out["error"]
    status, out = api(port, "open_path", [str(Path.home())])
    assert out["ok"] is False and "inside the workspace" in out["error"]
    status, out = api(port, "open_url", ["file:///etc/passwd"])
    assert out["ok"] is False


def test_dialogs_explain_themselves_in_browser_mode(server):
    port, _ = server
    _, out = api(port, "choose_csv")
    assert out["ok"] is False and "path" in out["error"]
