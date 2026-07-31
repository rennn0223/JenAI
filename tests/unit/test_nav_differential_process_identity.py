from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import jenai.acceptance.nav_differential_runner as runner


def _write_process(
    proc_root: Path,
    *,
    pid: int,
    ppid: int,
    start_ticks: int,
    cmdline: bytes,
    children: tuple[int, ...] = (),
    thread_children: dict[int, tuple[int, ...]] | None = None,
) -> None:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    stat_fields = ["S", str(ppid), *(["0"] * 17), str(start_ticks)]
    (process / "stat").write_text(
        f"{pid} (process {pid}) {' '.join(stat_fields)}\n",
        encoding="utf-8",
    )
    (process / "cmdline").write_bytes(cmdline)
    task_children = {pid: children, **(thread_children or {})}
    for tid, child_pids in task_children.items():
        task = process / "task" / str(tid)
        task.mkdir(parents=True)
        (task / "children").write_text(
            " ".join(str(child) for child in child_pids),
            encoding="utf-8",
        )


def _generation(processes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "boot_id": "12345678-1234-5678-1234-567812345678",
        "session": "nav2",
        "session_id": "$1",
        "session_created": 1000,
        "pane_id": "%1",
        "pane_pid": 101,
        "pane_start_ticks": 1001,
        "processes": processes,
    }


def test_process_tree_snapshot_captures_descendants_and_thread_children(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    _write_process(
        proc_root,
        pid=101,
        ppid=1,
        start_ticks=1001,
        cmdline=b"bash\0run-nav2\0",
        children=(102,),
        thread_children={111: (103,)},
    )
    _write_process(
        proc_root,
        pid=102,
        ppid=101,
        start_ticks=1002,
        cmdline=b"controller_server\0",
    )
    _write_process(
        proc_root,
        pid=103,
        ppid=101,
        start_ticks=1003,
        cmdline=b"planner_server\0",
    )

    snapshot = runner._process_tree_snapshot_once(101, proc_root=proc_root)

    assert snapshot == [
        {
            "pid": 101,
            "ppid": 1,
            "start_ticks": 1001,
            "cmdline_sha256": hashlib.sha256(b"bash\0run-nav2\0").hexdigest(),
        },
        {
            "pid": 102,
            "ppid": 101,
            "start_ticks": 1002,
            "cmdline_sha256": hashlib.sha256(b"controller_server\0").hexdigest(),
        },
        {
            "pid": 103,
            "ppid": 101,
            "start_ticks": 1003,
            "cmdline_sha256": hashlib.sha256(b"planner_server\0").hexdigest(),
        },
    ]


@pytest.mark.parametrize("missing", ["stat", "cmdline", "children"])
def test_process_tree_snapshot_fails_closed_on_missing_process_evidence(
    tmp_path: Path,
    missing: str,
) -> None:
    proc_root = tmp_path / "proc"
    _write_process(
        proc_root,
        pid=101,
        ppid=1,
        start_ticks=1001,
        cmdline=b"bash\0",
        children=(102,),
    )
    _write_process(
        proc_root,
        pid=102,
        ppid=101,
        start_ticks=1002,
        cmdline=b"controller_server\0",
    )
    target = (
        proc_root / "102" / missing
        if missing != "children"
        else proc_root / "102" / "task" / "102" / "children"
    )
    target.unlink()

    assert runner._process_tree_snapshot_once(101, proc_root=proc_root) is None


def test_stable_process_tree_snapshot_rejects_a_racing_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshots = [
        [{"pid": 101, "ppid": 1, "start_ticks": 1, "cmdline_sha256": "a" * 64}],
        [{"pid": 101, "ppid": 1, "start_ticks": 2, "cmdline_sha256": "a" * 64}],
    ]

    monkeypatch.setattr(
        runner,
        "_process_tree_snapshot_once",
        lambda _pid, *, proc_root: snapshots.pop(0),
    )

    assert runner._stable_process_tree_snapshot(101, proc_root=Path("/unused")) is None


def test_process_generation_validator_binds_descendant_identity() -> None:
    valid = _generation(
        [
            {"pid": 101, "ppid": 1, "start_ticks": 1001, "cmdline_sha256": "a" * 64},
            {"pid": 102, "ppid": 101, "start_ticks": 1002, "cmdline_sha256": "b" * 64},
        ]
    )
    assert runner._valid_nav2_process_generation(valid)

    for mutate in (
        lambda value: value.pop("processes"),
        lambda value: value["processes"].append(dict(value["processes"][1])),
        lambda value: value["processes"][1].update(ppid=999),
        lambda value: value["processes"][0].update(start_ticks=999),
        lambda value: value["processes"][1].update(cmdline_sha256="invalid"),
    ):
        candidate = _generation([dict(item) for item in valid["processes"]])
        mutate(candidate)
        assert not runner._valid_nav2_process_generation(candidate)


def test_runtime_fingerprint_changes_when_only_descendant_generation_changes() -> None:
    generation = _generation(
        [
            {"pid": 101, "ppid": 1, "start_ticks": 1001, "cmdline_sha256": "a" * 64},
            {"pid": 102, "ppid": 101, "start_ticks": 1002, "cmdline_sha256": "b" * 64},
        ]
    )
    identity = {"nav2_process_generation": generation}
    first = runner._runtime_fingerprint(identity)
    generation["processes"][1]["start_ticks"] = 2002

    assert runner._runtime_fingerprint(identity) != first


def test_runtime_identity_rejects_descendant_restart_during_capture(
    differential_artifact_factory: Any,
) -> None:
    artifact = differential_artifact_factory(mode="R1_bridge_nav2")
    identity = artifact["runtime_identity"]
    start = identity["nav2_process_generation"]
    end = deepcopy(start)
    end["processes"][1]["start_ticks"] = 2002
    identity["nav2_process_generation_end"] = end

    failures = runner._runtime_identity_failures(identity, require_end_generation=True)

    assert "nav2_process_generation_changed" in failures
