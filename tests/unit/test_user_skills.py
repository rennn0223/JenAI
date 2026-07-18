from __future__ import annotations

import asyncio
from pathlib import Path

from jenai.tools.user_skills import load_user_skills, skills_dir


def _write_skill(cfg_dir: Path, filename: str, body: str) -> None:
    d = cfg_dir / "skills"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(body, encoding="utf-8")


def test_loader_reads_valid_skill_and_defaults_name_to_stem(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    _write_skill(tmp_path, "inspect.toml", 'description = "巡檢"\nsteps = "大廳, 機械系館"\n')
    skills, warnings = load_user_skills(cfg)
    assert warnings == []
    assert skills["inspect"].steps == "大廳, 機械系館"
    assert skills["inspect"].description == "巡檢"


def test_loader_warns_and_skips_bad_files_without_raising(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    _write_skill(tmp_path, "broken.toml", "not = [valid")
    _write_skill(tmp_path, "stop.toml", 'steps = "大廳"\n')  # shadows built-in
    _write_skill(tmp_path, "nosteps.toml", 'description = "x"\n')
    _write_skill(tmp_path, "9bad.toml", 'steps = "大廳"\n')  # invalid name
    skills, warnings = load_user_skills(cfg)
    assert skills == {}
    assert len(warnings) == 4
    assert any("shadows a built-in" in w for w in warnings)


def test_loader_missing_dir_is_empty_not_error(tmp_path: Path) -> None:
    skills, warnings = load_user_skills(tmp_path / "config.toml")
    assert skills == {} and warnings == []
    assert skills_dir(tmp_path / "config.toml") == tmp_path / "skills"


def test_tui_runs_user_skill_as_mission_with_approval(monkeypatch, tmp_path: Path) -> None:
    from jenai.config.store import build_minimal_config
    from jenai.tools.mission_core import MissionReport, StepResult
    from jenai.tui.app import JenAITuiApp
    from jenai.tui.widgets import ApprovalCard

    _write_skill(tmp_path, "inspect.toml", 'description = "巡檢"\nsteps = "大廳, 機械系館"\n')

    ran = {}

    async def fake_run_mission(config, locations, steps, *, on_step=None, navigate=None):
        ran["steps"] = [(s.kind, s.target) for s in steps]
        return MissionReport([StepResult("goto", "大廳", "succeeded", "ok")])

    monkeypatch.setattr("jenai.tui.direct_execution.run_mission", fake_run_mission)

    config = build_minimal_config(
        provider_name="t", provider="openai", default_model="m", api_key_env=""
    )

    async def run() -> None:
        app = JenAITuiApp(config=config, config_path=tmp_path / "config.toml")
        async with app.run_test() as pilot:
            await app.handle_user_text("/skills")  # lists the loaded skill
            await app.handle_user_text("/inspect")
            cards = list(app.query(ApprovalCard))
            assert len(cards) == 1  # skill still goes through the approval card
            await pilot.press("enter")
            await pilot.pause()
            if app._active_task is not None:
                await app._active_task

    asyncio.run(run())
    assert ran["steps"] == [("goto", "大廳"), ("goto", "機械系館")]
