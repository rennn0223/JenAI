"""Release supply-chain contracts that must not silently regress."""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


PRIVATE_RELEASE_TRUTH = (
    "新版 workflow 會為 private release 產生 CycloneDX SBOM 與 `SHA256SUMS`；"
    "只有這些 assets 實際出現在該 GitHub Release 時才視為已發布，且 private path "
    "不會產生或宣稱 GitHub artifact attestations。"
)
PRIVATE_RELEASE_DOCS = (
    ROOT / "README.md",
    ROOT / "docs" / "QUICKSTART.md",
    ROOT / "docs" / "operations" / "ROLLBACK.md",
    ROOT / ".github" / "workflows" / "README.md",
    ROOT / "docs" / "product" / "PRODUCT_READINESS.md",
    ROOT / "docs" / "releases" / "v2.0.1.md",
)


def test_current_release_docs_do_not_claim_private_github_attestations() -> None:
    for path in PRIVATE_RELEASE_DOCS:
        text = path.read_text(encoding="utf-8")
        assert PRIVATE_RELEASE_TRUTH in text, path


def test_release_workflow_binds_assets_to_one_verified_tag_commit() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")

    resolve = workflow.index("Resolve and checkout the immutable release source")
    checkout = workflow.index('git checkout --detach "${source_sha}"')
    mismatch_gate = workflow.index('"${trigger_sha}" != "${source_sha}"')
    published_gate = workflow.index("Refuse mutation of an existing published release")
    remote_gate = workflow.index("Verify the remote tag still identifies the built commit")
    release = workflow.index("Release with artifacts")
    clobber = workflow.index('gh release upload "${TAG}" "${release_assets[@]}" --clobber')

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


def test_release_repeats_safety_gate_and_emits_visibility_appropriate_assets() -> None:
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
        "RELEASE_ASSET_MANIFEST",
        '"${release_assets[@]}"',
        "contains stale asset",
        "remote release asset set does not match the verified manifest",
    )
    for contract in required:
        assert contract in workflow

    assert workflow.count(
        "uses: actions/attest@59d89421af93a897026c735860bf21b6eb4f7b26 # v4.1.0"
    ) == 2
    public_only = (
        "if: github.event.repository.visibility == 'public' && "
        "steps.integrity_mode.outputs.attestations_enabled == 'true'"
    )
    assert workflow.count(public_only) == 2
    assert workflow.index("Safety-chain coverage gate") < workflow.index("Build wheel and sdist")
    assert workflow.index("Declare release integrity mode") < workflow.index(
        "Generate signed build provenance"
    )
    assert workflow.index("Generate signed SBOM attestation") < workflow.index(
        "Bundle optional public attestations and SHA-256 checksums"
    ) < workflow.index("Release with artifacts")


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
        "/artifacts/**",
        "/audit.sqlite3*",
        "/memory/",
        "/docs/local/thesis/",
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


_RELEASE_FILES = (
    "jenai-2.0.1-py3-none-any.whl",
    "jenai-2.0.1.tar.gz",
    "jenai-2.0.1-constraints.txt",
    "jenai-2.0.1.cdx.json",
)


def _seed_release_dist(repo: Path) -> None:
    dist = repo / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    # Build helpers or unrelated files must never enter the release allow-list.
    (dist / ".gitignore").write_text("*\n", encoding="utf-8")
    (dist / "extra-visible.txt").write_text("not a release asset\n", encoding="utf-8")
    for name in _RELEASE_FILES:
        (dist / name).write_text(f"fixture for {name}\n", encoding="utf-8")


def _write_release_manifest(repo: Path, manifest: Path) -> tuple[str, ...]:
    _seed_release_dist(repo)
    (repo / "dist" / "SHA256SUMS").write_text("fixture checksum\n", encoding="utf-8")
    paths = tuple(f"dist/{name}" for name in (*_RELEASE_FILES, "SHA256SUMS"))
    manifest.write_text("\n".join(paths) + "\n", encoding="utf-8")
    return paths


def _checksum_names(dist: Path) -> set[str]:
    lines = (dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    return {line.split(" *", 1)[1] for line in lines}


def test_release_integrity_mode_executes_private_skip_and_public_requirement(
    tmp_path: Path,
) -> None:
    script = _workflow_step_script("Declare release integrity mode")
    repo = tmp_path / "mode"
    repo.mkdir()

    private = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha="0" * 40,
        tag="v2.0.1",
        extra_env={"REPOSITORY_VISIBILITY": "private"},
    )
    assert private.returncode == 0, private.stderr + private.stdout
    assert "attestations_enabled=false" in (repo / "github-output.txt").read_text(
        encoding="utf-8"
    )
    assert "without provenance claims" in private.stdout

    public = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha="0" * 40,
        tag="v2.0.1",
        extra_env={"REPOSITORY_VISIBILITY": "public"},
    )
    assert public.returncode == 0, public.stderr + public.stdout
    assert "attestations_enabled=true" in (repo / "github-output.txt").read_text(
        encoding="utf-8"
    )


def test_checksum_bundle_executes_private_and_public_contracts(tmp_path: Path) -> None:
    script = _workflow_step_script(
        "Bundle optional public attestations and SHA-256 checksums"
    )
    provenance = tmp_path / "provenance.json"
    sbom_attestation = tmp_path / "sbom-attestation.json"
    provenance.write_text("provenance fixture\n", encoding="utf-8")
    sbom_attestation.write_text("sbom attestation fixture\n", encoding="utf-8")

    private_repo = tmp_path / "private"
    private_manifest = tmp_path / "private-assets.txt"
    _seed_release_dist(private_repo)
    private = _run_release_step(
        script,
        private_repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha="0" * 40,
        tag="v2.0.1",
        extra_env={
            "ATTESTATIONS_ENABLED": "false",
            "PROVENANCE_BUNDLE": str(provenance),
            "RELEASE_ASSET_MANIFEST": str(private_manifest),
            "REPOSITORY_VISIBILITY": "private",
            "RELEASE_VERSION": "2.0.1",
            "SBOM_ATTESTATION_BUNDLE": str(sbom_attestation),
        },
    )
    assert private.returncode == 0, private.stderr + private.stdout
    assert _checksum_names(private_repo / "dist") == set(_RELEASE_FILES)
    assert ".gitignore" not in _checksum_names(private_repo / "dist")
    assert "extra-visible.txt" not in _checksum_names(private_repo / "dist")
    assert private_manifest.read_text(encoding="utf-8").splitlines() == [
        *(f"dist/{name}" for name in _RELEASE_FILES),
        "dist/SHA256SUMS",
    ]
    assert not list((private_repo / "dist").glob("*.sigstore.json"))

    public_repo = tmp_path / "public"
    public_manifest = tmp_path / "public-assets.txt"
    _seed_release_dist(public_repo)
    missing = _run_release_step(
        script,
        public_repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha="0" * 40,
        tag="v2.0.1",
        extra_env={
            "ATTESTATIONS_ENABLED": "true",
            "PROVENANCE_BUNDLE": str(provenance),
            "RELEASE_ASSET_MANIFEST": str(public_manifest),
            "REPOSITORY_VISIBILITY": "public",
            "RELEASE_VERSION": "2.0.1",
            "SBOM_ATTESTATION_BUNDLE": "",
        },
    )
    assert missing.returncode != 0
    assert "missing its SBOM-attestation bundle" in missing.stdout

    public = _run_release_step(
        script,
        public_repo,
        event="workflow_dispatch",
        ref="refs/heads/main",
        sha="0" * 40,
        tag="v2.0.1",
        extra_env={
            "ATTESTATIONS_ENABLED": "true",
            "PROVENANCE_BUNDLE": str(provenance),
            "RELEASE_ASSET_MANIFEST": str(public_manifest),
            "REPOSITORY_VISIBILITY": "public",
            "RELEASE_VERSION": "2.0.1",
            "SBOM_ATTESTATION_BUNDLE": str(sbom_attestation),
        },
    )
    assert public.returncode == 0, public.stderr + public.stdout
    expected = set(_RELEASE_FILES) | {
        "jenai-2.0.1.provenance.sigstore.json",
        "jenai-2.0.1.sbom.sigstore.json",
    }
    assert _checksum_names(public_repo / "dist") == expected
    assert public_manifest.read_text(encoding="utf-8").splitlines() == [
        *(f"dist/{name}" for name in _RELEASE_FILES),
        "dist/jenai-2.0.1.provenance.sigstore.json",
        "dist/jenai-2.0.1.sbom.sigstore.json",
        "dist/SHA256SUMS",
    ]


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
    script = """#!/bin/sh
set -eu
printf '%s\n' "$*" >> "${GH_LOG}"
if [ "${FAKE_GH_EXIT:-0}" != 0 ]; then exit "${FAKE_GH_EXIT}"; fi
case "$*" in
  *'--json isDraft'*)
    if [ -n "${GH_STATE:-}" ] && [ -f "${GH_STATE}.published" ]; then
      printf 'false\n'
    else
      printf '%s\n' "${FAKE_DRAFT:-false}"
    fi
    ;;
  *'--json assets'*)
    if [ -n "${GH_STATE:-}" ] && [ -f "${GH_STATE}.uploaded" ]; then
      printf '%b' "${FAKE_ASSETS_AFTER:-}"
    else
      printf '%b' "${FAKE_ASSETS_BEFORE:-}"
    fi
    ;;
  'release upload '*|'release create '*)
    if [ -n "${GH_STATE:-}" ]; then : > "${GH_STATE}.uploaded"; fi
    ;;
  'release edit '*)
    if [ -n "${GH_STATE:-}" ]; then : > "${GH_STATE}.published"; fi
    ;;
esac
"""
    path.write_text(script, encoding="utf-8")
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
    manifest = tmp_path / "release-assets.txt"
    _write_release_manifest(repo, manifest)

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
            "RELEASE_ASSET_MANIFEST": str(manifest),
            "SOURCE_SHA": main_sha,
            "RELEASE_EXISTS": "true",
        },
    )

    assert result.returncode != 0
    assert "became published during this run and is immutable" in result.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    assert calls == ["release view v9.9.6 --json isDraft --jq .isDraft"]


def test_release_upload_uses_exact_manifest_and_rejects_stale_draft(
    tmp_path: Path,
) -> None:
    script = _workflow_step_script("Release with artifacts (or attach to an existing release)")
    repo, main_sha, _feature_sha = _release_repo(tmp_path / "repo")
    _git(repo, "switch", "main")
    _git(repo, "tag", "-a", "v9.9.7", main_sha, "-m", "release")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_fake_gh(fake_bin / "gh")
    log = tmp_path / "gh.log"
    state = tmp_path / "gh-state"
    manifest = tmp_path / "release-assets.txt"
    paths = _write_release_manifest(repo, manifest)
    expected_names = tuple(Path(item).name for item in paths)
    expected_remote = "\n".join(expected_names) + "\n"
    common = {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "GH_LOG": str(log),
        "GH_STATE": str(state),
        "FAKE_DRAFT": "true",
        "FAKE_ASSETS_AFTER": expected_remote,
        "RELEASE_ASSET_MANIFEST": str(manifest),
        "SOURCE_SHA": main_sha,
        "RELEASE_EXISTS": "true",
    }

    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.7",
        sha=main_sha,
        tag="v9.9.7",
        extra_env={**common, "FAKE_ASSETS_BEFORE": f"{expected_names[0]}\n"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    upload = next(call for call in calls if call.startswith("release upload "))
    for release_path in paths:
        assert release_path in upload
    assert ".gitignore" not in upload
    assert "extra-visible.txt" not in upload
    assert upload.endswith(" --clobber")

    log.write_text("", encoding="utf-8")
    Path(f"{state}.uploaded").unlink(missing_ok=True)
    result = _run_release_step(
        script,
        repo,
        event="push",
        ref="refs/tags/v9.9.7",
        sha=main_sha,
        tag="v9.9.7",
        extra_env={**common, "FAKE_ASSETS_BEFORE": "stale.sigstore.json\n"},
    )
    assert result.returncode != 0
    assert "contains stale asset stale.sigstore.json" in result.stdout
    assert not any(
        call.startswith("release upload ")
        for call in log.read_text(encoding="utf-8").splitlines()
    )

    # A new manual release must remain a draft until the exact remote asset set
    # is checked, then publish with the reviewed notes.
    _git(repo, "tag", "-a", "v9.9.8", main_sha, "-m", "release")
    log.write_text("", encoding="utf-8")
    Path(f"{state}.uploaded").unlink(missing_ok=True)
    Path(f"{state}.published").unlink(missing_ok=True)
    result = _run_release_step(
        script,
        repo,
        event="workflow_dispatch",
        ref="refs/tags/v9.9.8",
        sha=main_sha,
        tag="v9.9.8",
        extra_env={**common, "FAKE_ASSETS_BEFORE": "", "RELEASE_EXISTS": "false"},
    )
    assert result.returncode == 0, result.stderr + result.stdout
    calls = log.read_text(encoding="utf-8").splitlines()
    create = next(call for call in calls if call.startswith("release create "))
    assert create.endswith(" --draft")
    assert all(release_path in create for release_path in paths)
    assert ".gitignore" not in create and "extra-visible.txt" not in create
    assert any(call.startswith("release edit ") for call in calls)
