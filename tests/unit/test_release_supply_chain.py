"""Release supply-chain contracts that must not silently regress."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_release_workflow_binds_assets_to_one_verified_tag_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    resolve = workflow.index("Resolve and checkout the immutable release source")
    checkout = workflow.index('git checkout --detach "${source_sha}"')
    mismatch_gate = workflow.index('"${trigger_sha}" != "${source_sha}"')
    published_gate = workflow.index("Refuse mutation of an existing published release")
    remote_gate = workflow.index("Verify the remote tag still identifies the built commit")
    release = workflow.index("Release with artifacts")
    clobber = workflow.index('gh release upload "${TAG}" dist/* --clobber')

    assert "fetch-depth: 0" in workflow
    assert resolve < mismatch_gate < checkout < published_gate < remote_gate < release < clobber
    assert 'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"' in workflow
    assert workflow.count('git rev-parse "refs/tags/${TAG}^{commit}"') >= 2
    assert '"${GITHUB_REF}" != "refs/heads/main"' in workflow
    assert '"${GITHUB_REF}" != "refs/tags/${TAG}"' in workflow
    assert 'git merge-base --is-ancestor "${source_sha}" "${origin_main_sha}"' in workflow
    assert "already published and immutable" in workflow
    assert "became published during this run and is immutable" in workflow
    assert "cancel-in-progress: false" in workflow


def test_release_repeats_safety_gate_and_emits_verifiable_assets() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    required = (
        "uv run pytest -q --cov=jenai --cov-report=term",
        "uv run coverage report --fail-under=90",
        "pip-audit==2.10.1",
        "constraints.txt",
        'uv tool install --force --constraints "${constraints}"',
        "cyclonedx1.5",
        "dist/*-constraints.txt",
        "dist/*.cdx.json",
        "actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26 # v4.1.0",
        "provenance.sigstore.json",
        "sbom.sigstore.json",
        "SHA256SUMS",
        "sha256sum --check SHA256SUMS",
    )
    for contract in required:
        assert contract in workflow

    assert workflow.count(
        "uses: actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26 # v4.1.0"
    ) == 2
    assert workflow.index("Safety-chain coverage gate") < workflow.index("Build wheel and sdist")
    assert workflow.index("Generate signed build provenance") < workflow.index(
        "Release with artifacts"
    )


def test_sdist_explicitly_excludes_local_secrets_and_thesis_material() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    project = config["project"]
    assert project["authors"] == [{"name": "林書任 / LIN, SHU-JEN"}]
    assert project["maintainers"] == [{"name": "林書任 / LIN, SHU-JEN"}]
    assert project["urls"] == {
        "Homepage": "https://github.com/rennn0223/JenAI",
        "Repository": "https://github.com/rennn0223/JenAI",
        "Issues": "https://github.com/rennn0223/JenAI/issues",
    }

    excluded = set(config["tool"]["hatch"]["build"]["targets"]["sdist"]["exclude"])
    required = {
        "/.claude/settings.local.json",
        "/.env",
        "/.env.*",
        "/artifacts/",
        "/audit.sqlite3*",
        "/memory/",
        "/docs/PAPERS.md",
        "/docs/THESIS_*.md",
        "/docs/thesis-assets/",
        "/docs/thesis-v*-media/",
        "/scripts/build_thesis_*.py",
        "/scripts/export_thesis_markdown.py",
        "/*.docx",
        "/*.pdf",
        "/*.pptx",
    }
    assert required <= excluded


def test_all_workflow_actions_are_pinned_to_verified_upstream_commits() -> None:
    # Verified with `git ls-remote` against each upstream tag on 2026-07-18.
    pins = {
        "actions/checkout": ("9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0", "v7.0.0"),
        "astral-sh/setup-uv": ("11f9893b081a58869d3b5fccaea48c9e9e46f990", "v8.3.2"),
        "actions/upload-artifact": (
            "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
            "v7.0.1",
        ),
        "actions/attest": ("59d89421af93a897026c735860bf21b6eb4f7b26", "v4.1.0"),
    }
    workflows = [
        ROOT / ".github" / "workflows" / name
        for name in ("ci.yml", "security.yml", "release.yml")
    ]

    seen: set[str] = set()
    for path in workflows:
        text = path.read_text(encoding="utf-8")
        references = re.findall(r"\buses:\s+([^\s#]+)(?:\s+#\s+([^\s]+))?", text)
        assert references, path
        for reference, version_comment in references:
            repository, separator, commit = reference.partition("@")
            assert separator and repository in pins, (path, reference)
            expected_commit, expected_version = pins[repository]
            assert re.fullmatch(r"[0-9a-f]{40}", commit), (path, reference)
            assert (commit, version_comment) == (expected_commit, expected_version)
            seen.add(repository)

    assert seen == set(pins)


def test_supply_chain_workflow_pins_auditor_and_retains_commit_sbom() -> None:
    workflow = (ROOT / ".github" / "workflows" / "security.yml").read_text(
        encoding="utf-8"
    )

    assert "pip-audit==2.10.1" in workflow
    assert "jenai-sbom-${{ github.sha }}" in workflow
    assert 'assert d["bomFormat"] == "CycloneDX"' in workflow
    assert "if-no-files-found: error" in workflow
    assert "retention-days: 90" in workflow



def _workflow_step_script(name: str) -> str:
    """Extract one literal bash run block so tests execute the shipped policy."""

    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    marker = f"      - name: {name}\n"
    assert workflow.count(marker) == 1
    chunk = workflow.split(marker, 1)[1]
    chunk = chunk.split("\n      - name:", 1)[0]
    run_marker = "        run: |\n"
    assert chunk.count(run_marker) == 1
    lines = chunk.split(run_marker, 1)[1].splitlines()
    return "\n".join(line[10:] if line.startswith("          ") else line for line in lines)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    )
    return completed.stdout.strip()


def _release_repo(tmp_path: Path) -> tuple[Path, str, str]:
    """Create main plus one unmerged feature commit, mirroring checkout state."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        text=True,
        capture_output=True,
    )
    repo = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(origin), str(repo)], check=True, text=True, capture_output=True
    )
    _git(repo, "config", "user.name", "Release Test")
    _git(repo, "config", "user.email", "release-test@example.invalid")
    (repo / "state.txt").write_text("main\n", encoding="utf-8")
    _git(repo, "add", "state.txt")
    _git(repo, "commit", "-m", "main release source")
    _git(repo, "push", "origin", "main")
    main_sha = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-c", "feature")
    (repo / "state.txt").write_text("unmerged\n", encoding="utf-8")
    _git(repo, "commit", "-am", "unmerged release source")
    feature_sha = _git(repo, "rev-parse", "HEAD")
    return repo, main_sha, feature_sha


def _run_release_step(
    script: str,
    repo: Path,
    *,
    event: str,
    ref: str,
    sha: str,
    tag: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    output = repo / "github-output.txt"
    output.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        "GITHUB_EVENT_NAME": event,
        "GITHUB_REF": ref,
        "GITHUB_SHA": sha,
        "GITHUB_OUTPUT": str(output),
        "TAG": tag,
        **(extra_env or {}),
    }
    return subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, text=True, capture_output=True
    )


def test_release_source_state_machine_accepts_only_main_or_matching_tag(tmp_path: Path) -> None:
    script = _workflow_step_script("Resolve and checkout the immutable release source")

    repo, main_sha, _feature_sha = _release_repo(tmp_path / "new-main")
    result = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha=main_sha,
        tag="v9.9.1",
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "tag_exists=false" in (repo / "github-output.txt").read_text(encoding="utf-8")

    repo, _main_sha, feature_sha = _release_repo(tmp_path / "new-feature")
    result = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/heads/feature",
        sha=feature_sha,
        tag="v9.9.2",
    )
    assert result.returncode != 0
    assert "only from refs/heads/main" in result.stdout

    repo, main_sha, _feature_sha = _release_repo(tmp_path / "existing")
    _git(repo, "tag", "-a", "v9.9.3", main_sha, "-m", "release")
    _git(repo, "push", "origin", "refs/tags/v9.9.3")
    result = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha=main_sha,
        tag="v9.9.3",
    )
    assert result.returncode != 0
    assert "must be recovered from its tag ref" in result.stdout

    result = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/tags/v9.9.3",
        sha=main_sha,
        tag="v9.9.3",
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_tag_push_rejects_commit_not_reachable_from_origin_main(tmp_path: Path) -> None:
    script = _workflow_step_script("Resolve and checkout the immutable release source")
    repo, _main_sha, feature_sha = _release_repo(tmp_path)
    _git(repo, "tag", "-a", "v9.9.4", feature_sha, "-m", "unmerged")
    _git(repo, "push", "origin", "refs/tags/v9.9.4")

    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.4",
        sha=feature_sha,
        tag="v9.9.4",
    )

    assert result.returncode != 0
    assert "not reachable from origin/main" in result.stdout


def _write_fake_gh(path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"${GH_LOG}\"\n"
        "if [ \"${FAKE_GH_EXIT:-0}\" != 0 ]; then exit \"${FAKE_GH_EXIT}\"; fi\n"
        "printf '%s\\n' \"${FAKE_DRAFT:-false}\"\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def test_published_release_is_rejected_and_draft_is_recoverable(tmp_path: Path) -> None:
    script = _workflow_step_script("Refuse mutation of an existing published release")
    repo, main_sha, _feature_sha = _release_repo(tmp_path / "repo")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")
    log = tmp_path / "gh.log"

    common = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GH_LOG": str(log),
    }
    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.5",
        sha=main_sha,
        tag="v9.9.5",
        extra_env={**common, "FAKE_DRAFT": "false"},
    )
    assert result.returncode != 0
    assert "already published and immutable" in result.stdout

    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.5",
        sha=main_sha,
        tag="v9.9.5",
        extra_env={**common, "FAKE_DRAFT": "true"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    output = (repo / "github-output.txt").read_text(encoding="utf-8")
    assert "exists=true" in output and "draft=true" in output


def test_release_rechecks_draft_before_clobbering_assets(tmp_path: Path) -> None:
    script = _workflow_step_script("Release with artifacts (or attach to an existing release)")
    repo, main_sha, _feature_sha = _release_repo(tmp_path / "repo")
    _git(repo, "switch", "main")
    _git(repo, "tag", "-a", "v9.9.6", main_sha, "-m", "release")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")
    log = tmp_path / "gh.log"

    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.6",
        sha=main_sha,
        tag="v9.9.6",
        extra_env={
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GH_LOG": str(log),
            "FAKE_DRAFT": "false",
            "SOURCE_SHA": main_sha,
            "RELEASE_EXISTS": "true",
        },
    )

    assert result.returncode != 0
    assert "became published during this run and is immutable" in result.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == ["release view v9.9.6 --json isDraft --jq .isDraft"]
