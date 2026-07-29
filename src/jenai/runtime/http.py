"""Authenticated loopback HTTP transport for the Robot Runtime interface."""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jenai.runtime.contracts import RUNTIME_SCHEMA_VERSION, RuntimeState
from jenai.runtime.journal import RuntimeJournal
from jenai.runtime.responses import RuntimeHealth


class _RuntimeHandler(BaseHTTPRequestHandler):
    journal: RuntimeJournal
    token: str

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Keep the internal runtime transport quiet by default."""

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.UNAUTHORIZED:
            self.send_header("WWW-Authenticate", "Bearer")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        prefix = "Bearer "
        header = self.headers.get("Authorization", "")
        if not header.startswith(prefix):
            return False
        candidate = header[len(prefix) :]
        return bool(candidate) and hmac.compare_digest(candidate, self.token)

    def _reject_unauthorized(self) -> None:
        self._send_json(
            {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "error": "unauthorized",
                "message": "需要有效的 Robot Runtime access token。",
            },
            status=HTTPStatus.UNAUTHORIZED,
        )

    def _send_error_contract(
        self,
        *,
        status: HTTPStatus,
        error: str,
        message: str,
    ) -> None:
        self._send_json(
            {
                "schema_version": RUNTIME_SCHEMA_VERSION,
                "error": error,
                "message": message,
            },
            status=status,
        )

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._reject_unauthorized()
            return

        parsed = urlsplit(self.path)
        if parsed.path == "/v1/health":
            snapshot = self.journal.snapshot()
            health = RuntimeHealth(
                status="ok" if snapshot.state == RuntimeState.READY else "degraded",
                runtime_id=snapshot.runtime_id,
                state=snapshot.state,
                safety_epoch=snapshot.safety_epoch,
                last_sequence=snapshot.last_sequence,
            )
            self._send_json(health.model_dump(mode="json"))
            return

        if parsed.path == "/v1/runtime":
            self._send_json(self.journal.snapshot().model_dump(mode="json"))
            return

        if parsed.path == "/v1/events":
            query = parse_qs(parsed.query, keep_blank_values=True)
            raw_cursor = query.get("after_sequence", ["0"])[0]
            try:
                after_sequence = int(raw_cursor)
                if after_sequence < 0:
                    raise ValueError
            except ValueError:
                self._send_error_contract(
                    status=HTTPStatus.BAD_REQUEST,
                    error="invalid_request",
                    message="after_sequence 必須是大於或等於 0 的整數。",
                )
                return
            page = self.journal.events(after_sequence=after_sequence)
            self._send_json(page.model_dump(mode="json"))
            return

        self._send_error_contract(
            status=HTTPStatus.NOT_FOUND,
            error="not_found",
            message="找不到要求的 Robot Runtime 資源。",
        )


def make_runtime_server(
    journal: RuntimeJournal,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: str,
) -> ThreadingHTTPServer:
    """Create the authenticated runtime transport without starting its loop."""
    if not token:
        raise ValueError("token must not be blank")
    if host != "127.0.0.1":
        raise ValueError("host must be 127.0.0.1 until a secure remote transport is configured")
    handler = type(
        "JenAIRuntimeHandler",
        (_RuntimeHandler,),
        {
            "journal": journal,
            "token": token,
        },
    )
    return ThreadingHTTPServer((host, port), handler)
