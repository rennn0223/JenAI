from __future__ import annotations

from pathlib import Path

from jenai.config import save_config
from jenai.config.store import build_minimal_config
from jenai.doctor import run_doctor


def test_doctor_reports_missing_config(tmp_path: Path) -> None:
    result = run_doctor(tmp_path / "missing.toml")

    assert result.overall == "fail"
    assert any(item.section == "config" and item.status == "fail" for item in result.items)


def test_doctor_every_failure_has_a_fix_suggestion(tmp_path: Path) -> None:
    # F02 acceptance: every FAIL item carries an actionable fix_suggestion.
    result = run_doctor(tmp_path / "missing.toml")
    fails = [item for item in result.items if item.status == "fail"]
    assert fails
    assert all(item.fix_suggestion for item in fails)


def test_doctor_ros2_distinguishes_missing_cli_from_unsourced_env(monkeypatch) -> None:
    # F02 acceptance: ROS2 check separates "command missing" from "env not sourced".
    from jenai.doctor import checks

    monkeypatch.setattr(checks.shutil, "which", lambda name: None)
    missing = checks._check_ros2()
    assert missing[0].status == "fail"
    assert "not found on PATH" in missing[0].message

    monkeypatch.setattr(checks.shutil, "which", lambda name: "/opt/ros/jazzy/bin/ros2")

    class _Fail:
        returncode = 1
        stdout = ""
        stderr = ""

    monkeypatch.setattr(checks.subprocess, "run", lambda *a, **kw: _Fail())
    unsourced = checks._check_ros2()
    assert unsourced[0].status == "fail"
    assert "sourced" in (unsourced[0].fix_suggestion or "")


def test_doctor_reports_provider_and_models_from_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("JENAI_TEST_API_KEY", "test-key")
    path = tmp_path / "config.toml"
    config = build_minimal_config(
        provider_name="test",
        provider="openai",
        default_model="gpt-test",
        api_key_env="JENAI_TEST_API_KEY",
    )
    save_config(config, path)

    result = run_doctor(path)

    assert any(
        item.section == "provider"
        and item.check_name == "active_provider"
        and item.status == "pass"
        for item in result.items
    )
    assert any(
        item.section == "provider"
        and item.check_name == "model_bindings"
        and item.status == "pass"
        for item in result.items
    )


def test_doctor_none_config_path_resolves_locations_against_config_dir(
    tmp_path: Path, monkeypatch
) -> None:
    # run_doctor(None) must resolve the locations file against the real config
    # dir (default_config_path), not the current working directory.
    from jenai.doctor import checks

    monkeypatch.setenv("JENAI_TEST_API_KEY", "test-key")
    cfg_path = tmp_path / "config.toml"
    save_config(
        build_minimal_config(
            provider_name="test",
            provider="openai",
            default_model="gpt-test",
            api_key_env="JENAI_TEST_API_KEY",
        ),
        cfg_path,
    )
    (tmp_path / "locations.toml").write_text("# empty\n", encoding="utf-8")
    monkeypatch.setattr(checks, "default_config_path", lambda: cfg_path)

    result = run_doctor(None)  # None must fall back to default_config_path()

    loc = next(i for i in result.items if i.check_name == "locations_file")
    assert loc.status == "pass"
    assert str(tmp_path) in loc.message  # resolved via config dir, not cwd



def test_nav_stack_checks_read_the_graph(monkeypatch, tmp_path) -> None:
    """nav checks: PASS when the graph has the nav stack (Nav2 detected via
    `ros2 action list` — action topics are hidden from `ros2 topic list`),
    WARN with an ONBOARDING pointer when it doesn't; never FAIL."""
    from jenai.doctor.checks import _check_nav_stack

    monkeypatch.setattr("jenai.doctor.checks.shutil.which", lambda _: "/usr/bin/ros2")
    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.list_topics",
        lambda *, timeout=5.0: ["/map", "/amcl_pose", "/scan", "/cmd_vel"],
    )
    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.list_actions",
        lambda *, timeout=5.0: ["/navigate_to_pose"],
    )

    from jenai.adapters.ros2_adapter import TopicInfo

    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.topic_info",
        lambda topic, *, timeout=5.0: TopicInfo(name=topic, subscriber_count=2),
    )

    items = _check_nav_stack(None)

    assert [i.check_name for i in items] == ["map", "localization", "laser", "nav2", "cmd_vel"]
    assert all(str(i.status) == "pass" for i in items)


def test_nav_stack_warns_and_points_at_onboarding(monkeypatch) -> None:
    from jenai.doctor.checks import _check_nav_stack

    monkeypatch.setattr("jenai.doctor.checks.shutil.which", lambda _: "/usr/bin/ros2")
    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.list_topics",
        lambda *, timeout=5.0: ["/rosout", "/parameter_events"],
    )
    monkeypatch.setattr(
        "jenai.adapters.ros2_adapter.list_actions", lambda *, timeout=5.0: []
    )

    items = _check_nav_stack(None)

    assert all(str(i.status) == "warn" for i in items)  # never FAIL: must not block
    assert any("ONBOARDING" in (i.fix_suggestion or "") for i in items)


def test_nav_stack_skipped_without_ros2(monkeypatch) -> None:
    from jenai.doctor.checks import _check_nav_stack

    monkeypatch.setattr("jenai.doctor.checks.shutil.which", lambda _: None)
    assert _check_nav_stack(None) == []


def test_run_doctor_include_nav_flag(monkeypatch, tmp_path) -> None:
    from jenai.doctor.checks import run_doctor

    called = []
    monkeypatch.setattr(
        "jenai.doctor.checks._check_nav_stack", lambda config: called.append(1) or []
    )
    run_doctor(tmp_path / "config.toml", include_nav=False)
    assert called == []
    run_doctor(tmp_path / "config.toml")
    assert called == [1]
