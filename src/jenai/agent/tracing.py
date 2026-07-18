"""Execution trace of the current run — the data /why reads."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from agents import set_trace_processors
from agents.tracing import TracingProcessor

from jenai.secure_files import private_append_file, write_all


def _traces_path() -> Path:
    return Path.home() / ".config" / "jenai" / "traces" / "traces.jsonl"


class FileTracingProcessor(TracingProcessor):
    """Write openai-agents traces/spans to a local JSONL file.

    The SDK emits a rich trace of each run (agent turns, tool calls, handoffs,
    guardrails). By default it ships those spans to OpenAI's hosted backend. We
    replace that exporter entirely (see `install_local_tracing`) so nothing —
    task text, navigation goals, /cmd_vel payloads — is uploaded to a third
    party; instead this processor writes one JSON line per event locally, so
    `/run` behaviour can be inspected and later surfaced in the WebUI timeline.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _traces_path()
        self._lock = threading.Lock()

    def _write(self, record: dict) -> None:
        record["ts"] = datetime.now(UTC).isoformat(timespec="seconds")
        payload = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode()
        try:
            with self._lock, private_append_file(self._path) as fd:
                write_all(fd, payload)
        except OSError:
            pass  # tracing must never break a run

    def on_trace_start(self, trace) -> None:
        self._write({"event": "trace_start", "trace_id": trace.trace_id, "name": trace.name})

    def on_trace_end(self, trace) -> None:
        self._write({"event": "trace_end", "trace_id": trace.trace_id})

    def on_span_start(self, span) -> None:
        pass  # we log spans on completion (when timing/errors are known)

    def on_span_end(self, span) -> None:
        span_type = type(span.span_data).__name__.removesuffix("SpanData")
        self._write(
            {
                "event": "span",
                "type": span_type,
                "trace_id": span.trace_id,
                "error": str(span.error) if span.error else None,
            }
        )

    def force_flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


_installed = False


def install_local_tracing(path: Path | None = None) -> None:
    """Route SDK tracing to the local file processor once per process (idempotent).

    Uses ``set_trace_processors`` (which REPLACES the processor list) rather than
    ``add_trace_processor`` (which appends): appending would leave the SDK's
    default OpenAI backend exporter registered, so traces would still be uploaded
    when OPENAI_API_KEY is set — or log export errors every run when it is not.
    """
    global _installed
    if _installed:
        return
    set_trace_processors([FileTracingProcessor(path)])
    _installed = True
