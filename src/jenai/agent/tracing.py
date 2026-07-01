from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path

from agents import add_trace_processor
from agents.tracing import TracingProcessor


def _traces_path() -> Path:
    return Path.home() / ".config" / "jenai" / "traces" / "traces.jsonl"


class FileTracingProcessor(TracingProcessor):
    """Write openai-agents traces/spans to a local JSONL file.

    The SDK emits a rich trace of each run (agent turns, tool calls, handoffs,
    guardrails). Its default processor exports to OpenAI's backend, which we skip
    (no key). This processor makes the same events observable locally — one JSON
    line per event — so `/run` behaviour can be inspected and later surfaced in
    the WebUI timeline. Registered via `agents.add_trace_processor`.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _traces_path()
        self._lock = threading.Lock()

    def _write(self, record: dict) -> None:
        record["ts"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock, self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
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
    """Register the local file trace processor once per process (idempotent)."""
    global _installed
    if _installed:
        return
    add_trace_processor(FileTracingProcessor(path))
    _installed = True
