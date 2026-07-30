#!/usr/bin/env python3
"""Real-Firefox keyboard and responsive acceptance for JenAI UI surfaces.

Uses only Python's standard library plus the host's Firefox/geckodriver.  This
is an explicit acceptance gate, not part of the portable unit suite because CI
images are not required to contain a browser.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

from jenai.webui.render import render_dashboard_html, render_main

_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
_ARROW_DOWN = "\ue015"
_ARROW_RIGHT = "\ue014"
_END = "\ue010"
_ENTER = "\ue007"
_ESCAPE = "\ue00c"
_TAB = "\ue004"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _FixtureServer:
    def __init__(
        self,
        html_page: bytes,
        *,
        static_root: Path | None = None,
        routes: dict[str, tuple[bytes, str]] | None = None,
    ) -> None:

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                path = self.path.split("?", 1)[0]
                route = (routes or {}).get(path)
                if route is not None:
                    body, content_type = route
                elif static_root is not None:
                    candidate = (static_root / path.lstrip("/")).resolve()
                    if static_root.resolve() in candidate.parents and candidate.is_file():
                        body = candidate.read_bytes()
                        content_type = (
                            mimetypes.guess_type(candidate)[0] or "application/octet-stream"
                        )
                    else:
                        body, content_type = html_page, "text/html; charset=utf-8"
                else:
                    body, content_type = html_page, "text/html; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self) -> _FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


class _WebDriver:
    def __init__(self) -> None:
        if shutil.which("firefox") is None or shutil.which("geckodriver") is None:
            raise RuntimeError("Firefox and geckodriver are required for browser acceptance.")
        self.port = _free_port()
        self.process = subprocess.Popen(
            ["geckodriver", "--port", str(self.port)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        for _ in range(100):
            try:
                self._request("GET", "/status")
                break
            except (OSError, urllib.error.URLError):
                time.sleep(0.1)
        else:
            raise RuntimeError("geckodriver did not become ready.")
        value = self._request(
            "POST",
            "/session",
            {
                "capabilities": {
                    "alwaysMatch": {
                        "browserName": "firefox",
                        "acceptInsecureCerts": True,
                        "moz:firefoxOptions": {"args": ["-headless"]},
                    }
                }
            },
        )["value"]
        self.session_id = str(value["sessionId"])

    def _request(self, method: str, path: str, payload: object | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"WebDriver {method} {path} failed: {detail}") from exc

    def close(self) -> None:
        if getattr(self, "session_id", None):
            try:
                self._request("DELETE", f"/session/{self.session_id}")
            except Exception:
                pass
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()

    def navigate(self, url: str) -> None:
        self._request("POST", f"/session/{self.session_id}/url", {"url": url})

    def rect(self, width: int, height: int) -> None:
        self._request(
            "POST",
            f"/session/{self.session_id}/window/rect",
            {"width": width, "height": height, "x": 0, "y": 0},
        )

    def element(self, selector: str) -> str:
        value = self._request(
            "POST",
            f"/session/{self.session_id}/element",
            {"using": "css selector", "value": selector},
        )["value"]
        return str(value[_ELEMENT_KEY])

    def keys(self, element: str, text: str) -> None:
        self._request(
            "POST",
            f"/session/{self.session_id}/element/{element}/value",
            {"text": text, "value": list(text)},
        )

    def clear(self, element: str) -> None:
        self._request("POST", f"/session/{self.session_id}/element/{element}/clear", {})

    def script(self, script: str) -> Any:
        return self._request(
            "POST",
            f"/session/{self.session_id}/execute/sync",
            {"script": script, "args": []},
        )["value"]

    def screenshot(self, path: Path) -> None:
        encoded = self._request("GET", f"/session/{self.session_id}/screenshot")["value"]
        path.write_bytes(base64.b64decode(encoded))


@contextmanager
def _driver() -> Iterator[_WebDriver]:
    driver = _WebDriver()
    try:
        yield driver
    finally:
        driver.close()


def _website_html(root: Path) -> bytes:
    script = """
import { pathToFileURL } from 'node:url';
const url = pathToFileURL(process.cwd() + '/dist/server/index.js');
url.searchParams.set('browser-acceptance', `${process.pid}-${Date.now()}`);
const { default: worker } = await import(url.href);
const response = await worker.fetch(
  new Request('http://localhost/', { headers: { accept: 'text/html' } }),
  { ASSETS: { fetch: async () => new Response('Not found', { status: 404 }) } },
  { waitUntil() {}, passThroughOnException() {} },
);
process.stdout.write(await response.text());
"""
    return subprocess.check_output(
        ["node", "--input-type=module", "--eval", script],
        cwd=root / "website",
    )


def _webui_acceptance(root: Path, artifacts: Path | None) -> dict[str, object]:
    status = {
        "provider": "browser-test",
        "model": "gpt-test",
        "config_complete": True,
        "locations": 0,
        "doctor": {"overall": "pass", "items": []},
        "ros": {"available": False, "topics": [], "count": 0, "error": None},
        "transcript": [],
    }
    page = render_dashboard_html(status).encode()
    if artifacts is not None:
        (artifacts / "webui-after.html").write_bytes(page)
    routes = {
        "/fragment": (render_main(status).encode(), "text/html; charset=utf-8"),
        "/api/map": (b'{"locations":[],"pose":null}', "application/json"),
        "/api/topics": (b'{"available":false,"topics":[]}', "application/json"),
    }
    with _FixtureServer(page, routes=routes) as fixture, _driver() as browser:
        browser.rect(1440, 1000)
        browser.navigate(fixture.url)
        time.sleep(0.4)
        if artifacts is not None:
            browser.screenshot(artifacts / "webui-after-desktop.png")
        input_id = browser.element("#cmdinput")
        _require(
            browser.script("return document.activeElement.id") == "cmdinput",
            "WebUI input lost initial focus",
        )
        browser.keys(input_id, "/")
        state = browser.script(
            "return {display:getComputedStyle(document.querySelector('#palette')).display,"
            "count:document.querySelectorAll('#palette .pal-row').length};"
        )
        _require(state["display"] == "block" and state["count"] > 1, "Slash palette did not open")
        before = browser.script(
            "return document.querySelector('#palette .sel .pal-name').textContent"
        )
        browser.keys(input_id, _ARROW_DOWN)
        after = browser.script(
            "return document.querySelector('#palette .sel .pal-name').textContent"
        )
        _require(before != after, "ArrowDown did not move slash selection")
        browser.keys(input_id, _TAB)
        completed = browser.script(
            "return {value:document.querySelector('#cmdinput').value,"
            "focus:document.activeElement.id};"
        )
        _require(
            completed["value"].startswith("/") and completed["focus"] == "cmdinput",
            "Tab completion broke input focus",
        )

        console_tab = browser.element("#tab-console")
        browser.keys(console_tab, _ARROW_RIGHT)
        tab_state = browser.script(
            "return {focus:document.activeElement.id,"
            "selected:document.querySelector('[role=tab][aria-selected=true]').id,"
            "view:document.body.className};"
        )
        _require(
            tab_state == {"focus": "tab-camera", "selected": "tab-camera", "view": "view-camera"},
            "ArrowRight did not activate/focus the next tab",
        )
        browser.keys(browser.element("#tab-camera"), _END)
        _require(
            browser.script("return document.activeElement.id") == "tab-api",
            "End did not focus the last tab",
        )

        browser.rect(390, 844)
        browser.navigate(fixture.url)
        time.sleep(0.3)
        mobile = browser.script(
            "return {inner:innerWidth,scroll:document.documentElement.scrollWidth,"
            "composer:document.querySelector('#cmdform').getBoundingClientRect().right,"
            "tabs:getComputedStyle(document.querySelector('#tabs')).display};"
        )
        _require(
            mobile["scroll"] <= mobile["inner"] and mobile["composer"] <= mobile["inner"],
            "WebUI overflows the phone viewport",
        )
        _require(mobile["tabs"] == "flex", "Mobile WebUI tabs are not visible")
        if artifacts is not None:
            browser.screenshot(artifacts / "webui-browser-mobile.png")
    return {"slash_keyboard": "pass", "tab_keyboard": "pass", "mobile_390x844": "pass"}


def _website_acceptance(root: Path, artifacts: Path | None) -> dict[str, object]:
    site = root / "website"
    html_page = _website_html(root)
    with (
        _FixtureServer(html_page, static_root=site / "dist" / "client") as fixture,
        _driver() as browser,
    ):
        browser.rect(1200, 800)
        browser.navigate(fixture.url)
        time.sleep(0.8)
        search = browser.element("input[role=combobox]")
        browser.keys(search, "nav")
        first = browser.script(
            "return document.querySelector('[role=combobox]').getAttribute('aria-activedescendant')"
        )
        browser.keys(search, _ARROW_DOWN)
        second = browser.script(
            "return document.querySelector('[role=combobox]').getAttribute('aria-activedescendant')"
        )
        _require(
            first and second and first != second,
            "Website ArrowDown did not move the active search result",
        )
        browser.keys(search, _ESCAPE)
        escaped = browser.script(
            "return {expanded:document.querySelector('[role=combobox]')"
            ".getAttribute('aria-expanded'),"
            "focus:document.activeElement.getAttribute('role')};"
        )
        _require(
            escaped == {"expanded": "false", "focus": "combobox"},
            "Escape did not close search while preserving focus",
        )

        browser.rect(390, 844)
        browser.navigate(fixture.url)
        time.sleep(0.5)
        menu = browser.element(".menu-button")
        browser.keys(menu, _ENTER)
        mobile = browser.script(
            "return {expanded:document.querySelector('.menu-button').getAttribute('aria-expanded'),"
            "open:document.querySelector('#documentation-navigation').classList.contains('sidebar-open'),"
            "inner:innerWidth,scroll:document.documentElement.scrollWidth,focus:document.activeElement.className};"
        )
        _require(
            mobile["expanded"] == "true" and mobile["open"],
            "Enter did not open the mobile documentation menu",
        )
        _require(
            mobile["scroll"] <= mobile["inner"],
            "Documentation website overflows the phone viewport",
        )
        _require("menu-button" in mobile["focus"], "Mobile menu button lost keyboard focus")
        if artifacts is not None:
            browser.screenshot(artifacts / "website-browser-mobile.png")
    return {"search_keyboard": "pass", "mobile_menu_keyboard": "pass", "mobile_390x844": "pass"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifacts-dir", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifacts = args.artifacts_dir
    if artifacts is not None:
        artifacts.mkdir(parents=True, exist_ok=True)
    result = {
        "schema_version": 1,
        "browser": "Firefox headless via WebDriver",
        "webui": _webui_acceptance(root, artifacts),
        "website": _website_acceptance(root, artifacts),
    }
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if artifacts is not None:
        (artifacts / "browser-acceptance.json").write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
