"""PerceptionLoop: continuous camera → VLM → structured SceneAnalysis.

Where `/vision camera` is a one-shot snapshot with free-text output, this
module keeps watching: it captures frames at a configurable rate, asks the
vision model for a STRUCTURED read (scene context, objects, affordances,
suggested action), and hands each analysis to a callback — the TUI renders
it, and affordances can drive daemon rules like any numeric threshold.

Safety: perception itself never actuates. A `suggested_action` is a string
for humans (or for rules whose actions pass the existing approval/gating
machinery); nothing here bypasses an approval.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jenai.bridge import BridgeError, RosBridgeClient
from jenai.config.models import AppConfig
from jenai.providers.chat import ask_vision_json
from jenai.schemas import SceneAnalysis
from jenai.tools.vision_core import _to_data_url  # same encoding as one-shot vision

PERCEPTION_PROMPT = (
    "You are the continuous perception module of a ROS2 mobile robot. "
    "Analyze the camera frame and respond with ONLY JSON matching exactly: "
    '{"scene_context": "one-sentence description", '
    '"objects": ["visible objects"], '
    '"affordances": ["snake_case action opportunities, e.g. path_clear, '
    'path_blocked, door_open, person_present, obstacle_ahead"], '
    '"suggested_action": "one short imperative or empty string", '
    '"confidence": 0.0, '
    '"requires_approval": true}\n'
    "confidence is your 0-1 certainty about the affordances. "
    "requires_approval must be true unless the suggestion is purely informational."
)


def parse_scene_analysis(parsed: Any, *, ts: float | None = None) -> SceneAnalysis | None:
    """Build a SceneAnalysis from a (lenient) VLM JSON reply.

    Tolerates missing/odd fields the way thinking models produce them:
    strings where lists were asked, numeric strings for confidence, absent
    requires_approval (defaults True — never fail open on the safety flag).
    Returns None when there's no usable dict at all.
    """
    if not isinstance(parsed, dict):
        return None

    def _as_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value in (None, ""):
            return []
        return [str(value).strip()]

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    requires = parsed.get("requires_approval")
    if not isinstance(requires, bool):
        requires = True  # unknown → keep the human gate

    return SceneAnalysis(
        scene_context=str(parsed.get("scene_context", "")).strip(),
        objects=_as_list(parsed.get("objects")),
        affordances=[
            a.strip().lower().replace(" ", "_") for a in _as_list(parsed.get("affordances"))
        ],
        suggested_action=str(parsed.get("suggested_action", "")).strip(),
        confidence=confidence,
        requires_approval=requires,
        ts=time.time() if ts is None else ts,
    )


async def analyze_frame(
    config: AppConfig, bridge: RosBridgeClient, topic: str, *, timeout: float = 5.0
) -> SceneAnalysis | None:
    """One perception tick: capture a frame, ask the VLM, parse structurally.

    Returns None when the model gives nothing usable (the loop reports it
    honestly instead of inventing a scene). Raises BridgeError when the
    camera itself is unreachable.
    """
    frame_path = await bridge.capture_frame(topic, timeout=timeout)
    try:
        parsed = await ask_vision_json(
            config, PERCEPTION_PROMPT, _to_data_url(frame_path), binding="vision"
        )
    finally:
        frame_path.unlink(missing_ok=True)
    return parse_scene_analysis(parsed)


class PerceptionLoop:
    """Continuously perceive at `hz` and hand every SceneAnalysis to a callback.

    The callback runs on the loop's task; keep it light (mount a widget, feed
    a rule engine). Errors are throttled to one status report per streak so a
    dead camera doesn't flood the timeline.
    """

    def __init__(
        self,
        config: AppConfig,
        bridge: RosBridgeClient,
        *,
        topic: str | None = None,
        hz: float = 1.0,
        on_analysis: Callable[[SceneAnalysis], Awaitable[None]] | None = None,
        on_status: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._config = config
        self._bridge = bridge
        self.topic = topic or config.vehicle.camera_topic
        self.interval = 1.0 / max(0.05, hz)
        self._on_analysis = on_analysis
        self._on_status = on_status
        self._task: asyncio.Task[None] | None = None
        self.latest: SceneAnalysis | None = None
        self.frames = 0

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        error_streak = 0
        while True:
            started = time.monotonic()
            try:
                analysis = await analyze_frame(self._config, self._bridge, self.topic)
            except BridgeError as exc:
                error_streak += 1
                if error_streak == 1 and self._on_status is not None:
                    await self._on_status(f"perception: camera unavailable — {exc}")
            else:
                if analysis is None:
                    error_streak += 1
                    if error_streak == 1 and self._on_status is not None:
                        await self._on_status(
                            "perception: vision model returned no structured analysis"
                        )
                else:
                    if error_streak and self._on_status is not None:
                        await self._on_status("perception: recovered")
                    error_streak = 0
                    self.latest = analysis
                    self.frames += 1
                    if self._on_analysis is not None:
                        await self._on_analysis(analysis)
            # Pace to `hz` counting the work we just did; never busy-spin.
            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.05, self.interval - elapsed))
