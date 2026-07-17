"""Phase 1: multi-agent handoffs, cross-session memory, local tracing."""

from __future__ import annotations

import asyncio
import multiprocessing
import threading
from types import SimpleNamespace

from jenai.agent.session import JenAIFileSession
from jenai.agent.specialists import (
    build_motion_agent,
    build_navigation_agent,
    build_ros_developer_agent,
    build_ros_explorer_agent,
    build_supervisor_agent,
)
from jenai.agent.tracing import FileTracingProcessor, install_local_tracing
from jenai.config.store import build_minimal_config


def _config():
    return build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )


def _hold_session_transaction(directory, entered, release) -> None:
    session = JenAIFileSession("process-shared", directory=directory)
    with session._transaction():
        entered.set()
        release.wait(timeout=5)


def _enter_session_transaction(directory, acquired) -> None:
    session = JenAIFileSession("process-shared", directory=directory)
    with session._transaction():
        acquired.set()


def _handoff_names(agent) -> set[str]:
    names = set()
    for h in agent.handoffs:
        names.add(getattr(h, "name", None) or getattr(h, "agent_name", None))
    return names


def test_supervisor_hands_off_to_specialists() -> None:
    sup = build_supervisor_agent(_config())
    assert sup.name == "JenAI"
    assert _handoff_names(sup) == {
        "ROS Developer",
        "ROS Explorer",
        "Motion",
        "Navigation",
        "Perception",
    }
    supervisor_tools = {tool.name for tool in sup.tools}
    assert "explore_area_tool" in supervisor_tools
    assert "route_execute_tool" not in supervisor_tools


def test_specialists_carry_focused_toolsets() -> None:
    explorer = build_ros_explorer_agent(_config())
    motion = build_motion_agent(_config())
    explorer_tools = {t.name for t in explorer.tools}
    motion_tools = {t.name for t in motion.tools}
    # Explorer is read-only; Motion owns the actuation tools.
    assert "ros_drive_execute_tool" not in explorer_tools
    assert "ros_drive_execute_tool" in motion_tools
    assert "ros_topics_tool" in explorer_tools

    developer = build_ros_developer_agent(_config())
    developer_tools = {t.name for t in developer.tools}
    assert {"ros_topics_tool", "ros_schema_tool", "ros_drive_verified_tool", "ros_state_tool"} <= (
        developer_tools
    )
    assert "ros_drive_execute_tool" not in developer_tools
    assert "shell_run_tool" not in developer_tools

    navigation = build_navigation_agent(_config())
    navigation_tools = {tool.name for tool in navigation.tools}
    assert "explore_area_tool" in navigation_tools
    assert "ros_drive_execute_tool" not in navigation_tools


def test_session_roundtrip(tmp_path) -> None:
    session = JenAIFileSession("s1", directory=tmp_path)

    async def run():
        assert await session.get_items() == []
        await session.add_items([{"role": "user", "content": "hi"}])
        await session.add_items([{"role": "assistant", "content": "hello"}])
        items = await session.get_items()
        assert len(items) == 2
        assert (await session.get_items(limit=1))[0]["content"] == "hello"
        popped = await session.pop_item()
        assert popped["content"] == "hello"
        assert len(await session.get_items()) == 1
        await session.clear_session()
        assert await session.get_items() == []

    asyncio.run(run())


def test_session_persists_across_instances(tmp_path) -> None:
    async def run():
        first = JenAIFileSession("s2", directory=tmp_path)
        await first.add_items([{"role": "user", "content": "x"}])
        # A fresh instance (simulating a restart) sees the persisted items.
        reloaded = JenAIFileSession("s2", directory=tmp_path)
        assert len(await reloaded.get_items()) == 1

    asyncio.run(run())


def test_session_serializes_writes_across_instances(tmp_path, monkeypatch) -> None:
    """Two fresh Session objects for one path must not lose either update."""
    first = JenAIFileSession("shared", directory=tmp_path)
    second = JenAIFileSession("shared", directory=tmp_path)
    barrier = threading.Barrier(2)

    original_load = JenAIFileSession._load

    def synchronized_load(self):
        # If the instances have different locks, both enter _load together and
        # then overwrite the same original history. A shared lock makes the
        # second call wait, so the barrier deliberately breaks after timeout.
        try:
            barrier.wait(timeout=0.1)
        except threading.BrokenBarrierError:
            pass
        return original_load(self)

    monkeypatch.setattr(JenAIFileSession, "_load", synchronized_load)

    errors: list[BaseException] = []

    def append(session, content):
        try:
            session._append([{"content": content}])
        except BaseException as exc:  # surface worker failures in the test thread
            errors.append(exc)

    threads = [
        threading.Thread(target=append, args=(first, "first")),
        threading.Thread(target=append, args=(second, "second")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    items = original_load(first)
    assert {item["content"] for item in items} == {"first", "second"}


def test_session_lock_is_exclusive_across_processes(tmp_path) -> None:
    ctx = multiprocessing.get_context("spawn")
    entered = ctx.Event()
    release = ctx.Event()
    acquired = ctx.Event()
    holder = ctx.Process(
        target=_hold_session_transaction,
        args=(tmp_path, entered, release),
    )
    waiter = ctx.Process(
        target=_enter_session_transaction,
        args=(tmp_path, acquired),
    )

    holder.start()
    assert entered.wait(timeout=5)
    waiter.start()
    assert not acquired.wait(timeout=0.2), "a second process entered the locked transaction"

    release.set()
    assert acquired.wait(timeout=5)
    holder.join(timeout=5)
    waiter.join(timeout=5)
    assert holder.exitcode == waiter.exitcode == 0


def test_session_get_items_limit_zero_returns_empty(tmp_path) -> None:
    # limit=0 means "the last zero items"; a falsy check would wrongly return all.
    session = JenAIFileSession("s3", directory=tmp_path)

    async def run():
        await session.add_items([{"content": "a"}, {"content": "b"}])
        assert await session.get_items(limit=0) == []
        assert len(await session.get_items(limit=None)) == 2

    asyncio.run(run())


def test_session_history_is_capped(tmp_path) -> None:
    from jenai.agent import session as session_mod

    session = JenAIFileSession("s4", directory=tmp_path)

    async def run():
        await session.add_items([{"n": i} for i in range(session_mod._MAX_ITEMS + 50)])
        items = await session.get_items()
        assert len(items) == session_mod._MAX_ITEMS
        assert items[-1]["n"] == session_mod._MAX_ITEMS + 49  # newest kept

    asyncio.run(run())


def test_file_tracing_processor_writes(tmp_path) -> None:
    path = tmp_path / "traces.jsonl"
    proc = FileTracingProcessor(path)
    proc.on_trace_start(SimpleNamespace(trace_id="t1", name="JenAI /run"))
    proc.on_span_end(SimpleNamespace(span_data=SimpleNamespace(), trace_id="t1", error=None))
    proc.on_trace_end(SimpleNamespace(trace_id="t1"))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert '"event": "trace_start"' in lines[0]


def test_install_local_tracing_is_idempotent() -> None:
    install_local_tracing()
    install_local_tracing()  # must not raise or double-register
