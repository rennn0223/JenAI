"""Concrete read-only source for the repository-owned Isaac/ROS companion."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import math
import os
import signal
import stat
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TypeVar

from pydantic import BaseModel

from jenai.acceptance.motion_safety import (
    ClearanceBudget,
    ClearanceSourceEvidence,
    CollisionStreamEvidence,
    CostmapLayerEvidence,
    MotionRequestBinding,
    NavFootprintEvidence,
    PathEvidence,
    ProbeIdentityEvidence,
    RuntimeBinding,
    UsdCollisionGeometryEvidence,
    write_bytes_create_once,
)

_MAX_PROBE_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_PROBE_CONFIG_BYTES = 4 * 1024 * 1024
_MAX_SOURCE_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_PROBE_TIMEOUT_S = 181.0
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_ROOT = _REPOSITORY_ROOT / "src"
_PROBE_ENTRYPOINT = _REPOSITORY_ROOT / "scripts" / "isaac_motion_readiness_probe.py"
_TRUSTED_GIT = Path("/usr/bin/git")
_PROBE_PYTHON_EXECUTABLE = _REPOSITORY_ROOT / ".venv-ros" / "bin" / "python"
_PROBE_ENVIRONMENT_KEYS = (
    "AMENT_PREFIX_PATH",
    "CYCLONEDDS_URI",
    "FASTRTPS_DEFAULT_PROFILES_FILE",
    "HOME",
    "ISAAC_PATH",
    "LD_LIBRARY_PATH",
    "RMW_IMPLEMENTATION",
    "ROS_DOMAIN_ID",
    "ROS_SECURITY_ENABLE",
    "ROS_SECURITY_ENCLAVE_OVERRIDE",
    "ROS_SECURITY_KEYSTORE",
    "ROS_SECURITY_STRATEGY",
    "XDG_RUNTIME_DIR",
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trusted_git(*args: str) -> bytes:
    return subprocess.run(
        [str(_TRUSTED_GIT), *args],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        timeout=10.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout


def _reviewed_source_bundle(expected_git_sha: str) -> bytes:
    """Build the import bundle from immutable Git objects, never worktree bytes."""

    names = (
        _trusted_git(
            "ls-tree",
            "-r",
            "--name-only",
            expected_git_sha,
            "--",
            "src/jenai",
        )
        .decode()
        .splitlines()
    )
    python_paths = tuple(sorted(name for name in names if name.endswith(".py")))
    if not python_paths:
        raise ValueError("reviewed Git revision contains no JenAI Python source")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for repository_path in python_paths:
            content = _trusted_git("show", f"{expected_git_sha}:{repository_path}")
            archive.writestr(repository_path.removeprefix("src/"), content)
        archive.writestr(
            "jenai/_motion_safety_source_manifest.json",
            json.dumps(
                {"source_git_sha": expected_git_sha},
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    payload = buffer.getvalue()
    if not payload or len(payload) > _MAX_SOURCE_BUNDLE_BYTES:
        raise ValueError("reviewed source bundle exceeds its size limit")
    return payload


def prepare_reviewed_stage_export_bundle(config_path: Path, output_path: Path) -> dict[str, str]:
    """Persist the exact reviewed source closure for an in-Isaac Stage export."""

    if not config_path.is_absolute() or not output_path.is_absolute():
        raise ValueError("Stage export preparation paths must be absolute")
    snapshot = _bounded_file_bytes(config_path, _MAX_PROBE_CONFIG_BYTES)
    payload = json.loads(snapshot)
    if not isinstance(payload, dict):
        raise ValueError("Isaac observation config must be a JSON object")
    expected_git_sha = str(payload["runtime"]["git_sha"])
    _attest_repository_source(snapshot)
    bundle = _reviewed_source_bundle(expected_git_sha)
    _attest_repository_source(snapshot)
    write_bytes_create_once(output_path, bundle)
    return {
        "source_bundle_path": str(output_path),
        "source_bundle_sha256": hashlib.sha256(bundle).hexdigest(),
        "stage_entrypoint_path": str(
            _REPOSITORY_ROOT / "scripts" / "isaac_motion_readiness_stage_export.py"
        ),
        "stage_entrypoint_sha256": _sha256_file(
            _REPOSITORY_ROOT / "scripts" / "isaac_motion_readiness_stage_export.py"
        ),
    }


def _ros_dependency_roots() -> tuple[str, ...]:
    allowed_prefixes = (
        Path("/opt/ros").resolve(),
        Path("/home/nvidia/IsaacSim-ros_workspaces").resolve(),
    )
    roots = []
    for value in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if not value:
            continue
        path = Path(value).resolve()
        if path.is_dir() and any(path.is_relative_to(prefix) for prefix in allowed_prefixes):
            roots.append(str(path))
    return tuple(dict.fromkeys(roots))


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sha256_fd(fd: int) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _private_readonly_fd(content: bytes, *, prefix: str, suffix: str) -> int:
    writable_fd, frozen_path_text = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    frozen_path = Path(frozen_path_text)
    try:
        view = memoryview(content)
        while view:
            written = os.write(writable_fd, view)
            if written <= 0:
                raise OSError("private snapshot write made no progress")
            view = view[written:]
        os.fsync(writable_fd)
        os.close(writable_fd)
        writable_fd = -1
        frozen_fd = os.open(frozen_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        frozen_path.unlink()
        return frozen_fd
    except BaseException:
        if writable_fd >= 0:
            os.close(writable_fd)
        frozen_path.unlink(missing_ok=True)
        raise


def _bounded_file_bytes(path: Path, maximum_bytes: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum_bytes:
            raise ValueError("Isaac observation config exceeds its size limit")
        content = os.read(descriptor, maximum_bytes + 1)
        if len(content) > maximum_bytes or os.read(descriptor, 1):
            raise ValueError("Isaac observation config exceeds its size limit")
        return content
    finally:
        os.close(descriptor)


def _open_verified_python_fd(path: Path, expected_sha256: str | None = None) -> int:
    resolved = path.resolve(strict=True)
    descriptor = os.open(resolved, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not info.st_mode & 0o111:
            raise ValueError("probe Python executable must resolve to an executable regular file")
        if expected_sha256 is not None and _sha256_fd(descriptor) != expected_sha256:
            raise RuntimeError("probe Python executable identity changed")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _immutable_probe_fd(expected_sha256: str) -> int:
    content = _bounded_file_bytes(_PROBE_ENTRYPOINT, _MAX_PROBE_CONFIG_BYTES)
    if hashlib.sha256(content).hexdigest() != expected_sha256:
        raise RuntimeError("opened Isaac probe differs from reviewed entrypoint")
    return _private_readonly_fd(
        content,
        prefix="jenai-motion-readiness-probe-",
        suffix=".py",
    )


def _attest_repository_source(config_snapshot: bytes) -> None:
    payload = json.loads(config_snapshot)
    expected_git_sha = str(payload["runtime"]["git_sha"])
    head = subprocess.run(
        [str(_TRUSTED_GIT), "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout.strip()
    dirty = subprocess.run(
        [str(_TRUSTED_GIT), "status", "--porcelain"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=5.0,
        env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/nonexistent")},
    ).stdout
    if head != expected_git_sha or dirty:
        raise RuntimeError("repository probe source is not the reviewed clean Git revision")


def _checked_regular_file(path: Path, label: str) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError(f"{label} must be a regular non-symlink file")


class IsaacObservationOperation(StrEnum):
    RUNTIME_BINDING = "runtime_binding"
    MOTION_REQUEST = "motion_request"
    PLANNED_PATH = "planned_path"
    EFFECTIVE_NAV_FOOTPRINT = "effective_nav_footprint"
    USD_COLLISION_GEOMETRY = "usd_collision_geometry"
    COSTMAP_LAYERS = "costmap_layers"
    COLLISION_TIMELINE = "collision_timeline"
    CLEARANCE_BUDGET = "clearance_budget"
    CLEARANCE_SOURCES = "clearance_sources"


class IsaacReadOnlyTransport(Protocol):
    def probe_identity(self, source_git_sha: str) -> ProbeIdentityEvidence: ...

    async def observe(
        self,
        operation: IsaacObservationOperation,
        context: dict[str, object],
    ) -> object: ...


@dataclass(frozen=True)
class RepositoryIsaacReadOnlyTransport:
    """Run the fixed checked-in companion with bounded output and process cleanup."""

    config_path: Path
    timeout_s: float = 5.0
    environment: dict[str, str] = field(default_factory=dict)
    _config_snapshot: bytes = field(init=False, repr=False, compare=False)
    _source_bundle_snapshot: bytes = field(init=False, repr=False, compare=False)
    _dependency_roots: tuple[str, ...] = field(init=False, repr=False, compare=False)
    _python_executable: Path = field(init=False, repr=False, compare=False)
    _python_sha256: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.config_path.is_absolute():
            raise ValueError("Isaac observation config path must be absolute")
        _checked_regular_file(self.config_path, "Isaac observation config")
        snapshot = _bounded_file_bytes(self.config_path, _MAX_PROBE_CONFIG_BYTES)
        payload = json.loads(snapshot)
        if not isinstance(payload, dict):
            raise ValueError("Isaac observation config must be a JSON object")
        expected_git_sha = str(payload["runtime"]["git_sha"])
        python_executable = _PROBE_PYTHON_EXECUTABLE
        inner_timeout_s = float(payload.get("timeout_s", 5.0))
        minimum_outer_timeout_s = inner_timeout_s * 6.0 + 0.1
        if self.timeout_s < minimum_outer_timeout_s:
            raise ValueError("probe process timeout cannot cover plan and cancellation budgets")
        python_fd = _open_verified_python_fd(python_executable)
        try:
            python_sha256 = _sha256_fd(python_fd)
        finally:
            os.close(python_fd)
        _attest_repository_source(snapshot)
        object.__setattr__(self, "_config_snapshot", snapshot)
        object.__setattr__(
            self,
            "_source_bundle_snapshot",
            _reviewed_source_bundle(expected_git_sha),
        )
        object.__setattr__(self, "_dependency_roots", _ros_dependency_roots())
        object.__setattr__(self, "_python_executable", python_executable)
        object.__setattr__(self, "_python_sha256", python_sha256)
        _checked_regular_file(_PROBE_ENTRYPOINT, "repository Isaac observation probe")
        if (
            not math.isfinite(self.timeout_s)
            or self.timeout_s <= 0.0
            or self.timeout_s > _MAX_PROBE_TIMEOUT_S
        ):
            raise ValueError("Isaac observation probe timeout must be within (0, 181] seconds")
        unknown = set(self.environment).difference(_PROBE_ENVIRONMENT_KEYS)
        if unknown:
            raise ValueError(f"unsupported Isaac probe environment keys: {sorted(unknown)}")

    def _effective_environment(self) -> dict[str, str]:
        inherited = {
            key: value
            for key in _PROBE_ENVIRONMENT_KEYS
            if (value := os.environ.get(key)) is not None
        }
        inherited.update(self.environment)
        return inherited

    def probe_identity(self, source_git_sha: str) -> ProbeIdentityEvidence:
        environment = self._effective_environment() | {"dependency_roots": self._dependency_roots}
        return ProbeIdentityEvidence.create(
            source_git_sha=source_git_sha,
            entrypoint_path=_PROBE_ENTRYPOINT.relative_to(_REPOSITORY_ROOT).as_posix(),
            entrypoint_sha256=_sha256_file(_PROBE_ENTRYPOINT),
            source_bundle_sha256=hashlib.sha256(self._source_bundle_snapshot).hexdigest(),
            config_path=str(self.config_path),
            config_sha256=hashlib.sha256(self._config_snapshot).hexdigest(),
            python_executable=str(self._python_executable),
            python_executable_sha256=self._python_sha256,
            environment_sha256=_canonical_sha256(environment),
        )

    async def _read_bounded_output(
        self,
        process: asyncio.subprocess.Process,
        payload: bytes,
    ) -> bytes:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Isaac observation probe pipes are unavailable")
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        stdout = await process.stdout.read(_MAX_PROBE_RESPONSE_BYTES + 1)
        if len(stdout) > _MAX_PROBE_RESPONSE_BYTES:
            raise ValueError("Isaac observation probe response exceeds the size limit")
        return_code = await process.wait()
        if return_code != 0:
            raise RuntimeError("repository Isaac observation probe failed")
        return stdout

    async def observe(
        self,
        operation: IsaacObservationOperation,
        context: dict[str, object],
    ) -> object:
        _attest_repository_source(self._config_snapshot)
        before = self.probe_identity("")
        python_fd = _open_verified_python_fd(self._python_executable, self._python_sha256)
        entrypoint_fd = _immutable_probe_fd(before.entrypoint_sha256)
        config_fd = _private_readonly_fd(
            self._config_snapshot,
            prefix="jenai-motion-readiness-config-",
            suffix=".json",
        )
        source_fd = _private_readonly_fd(
            self._source_bundle_snapshot,
            prefix="jenai-motion-readiness-source-",
            suffix=".zip",
        )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._python_executable),
                "-I",
                f"/proc/self/fd/{entrypoint_fd}",
                "--source-bundle",
                f"/proc/self/fd/{source_fd}",
                *(item for root in self._dependency_roots for item in ("--dependency-root", root)),
                "--operation",
                operation.value,
                "--config",
                f"/proc/self/fd/{config_fd}",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._effective_environment(),
                executable=f"/proc/self/fd/{python_fd}",
                start_new_session=True,
                pass_fds=(python_fd, entrypoint_fd, config_fd, source_fd),
            )
            payload = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
            stdout = await asyncio.wait_for(
                self._read_bounded_output(process, payload),
                timeout=self.timeout_s,
            )
        finally:
            if process is not None:
                # Always reap the whole private process group. A successful parent may
                # otherwise leave a forked companion alive after returning JSON.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if process.returncode is None:
                    await process.wait()
            os.close(python_fd)
            os.close(entrypoint_fd)
            os.close(config_fd)
            os.close(source_fd)
        _attest_repository_source(self._config_snapshot)
        after = self.probe_identity("")
        if (
            before.entrypoint_sha256 != after.entrypoint_sha256
            or before.config_sha256 != after.config_sha256
            or before.environment_sha256 != after.environment_sha256
        ):
            raise RuntimeError("Isaac observation probe identity changed during collection")
        try:
            return json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Isaac observation probe returned malformed JSON") from exc


_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass
class IsaacRosReadOnlyEvidenceSource:
    """Decode exact typed Evidence from the checked-in ROS/Isaac companion."""

    transport: IsaacReadOnlyTransport

    async def _model(
        self,
        operation: IsaacObservationOperation,
        model: type[_ModelT],
        context: dict[str, object],
    ) -> _ModelT:
        return model.model_validate(await self.transport.observe(operation, context))

    async def runtime_binding(self) -> RuntimeBinding:
        return await self._model(
            IsaacObservationOperation.RUNTIME_BINDING,
            RuntimeBinding,
            {},
        )

    async def probe_identity(self, runtime: RuntimeBinding) -> ProbeIdentityEvidence:
        return self.transport.probe_identity(runtime.git_sha)

    async def motion_request(self, runtime: RuntimeBinding) -> MotionRequestBinding:
        return await self._model(
            IsaacObservationOperation.MOTION_REQUEST,
            MotionRequestBinding,
            {"runtime": runtime.model_dump(mode="json")},
        )

    async def planned_path(
        self,
        runtime: RuntimeBinding,
        request: MotionRequestBinding,
    ) -> PathEvidence:
        return await self._model(
            IsaacObservationOperation.PLANNED_PATH,
            PathEvidence,
            {
                "runtime": runtime.model_dump(mode="json"),
                "motion_request": request.model_dump(mode="json"),
            },
        )

    async def effective_nav_footprint(
        self,
        runtime: RuntimeBinding,
    ) -> NavFootprintEvidence:
        return await self._model(
            IsaacObservationOperation.EFFECTIVE_NAV_FOOTPRINT,
            NavFootprintEvidence,
            {"runtime": runtime.model_dump(mode="json")},
        )

    async def usd_collision_geometry(
        self,
        runtime: RuntimeBinding,
    ) -> UsdCollisionGeometryEvidence:
        return await self._model(
            IsaacObservationOperation.USD_COLLISION_GEOMETRY,
            UsdCollisionGeometryEvidence,
            {"runtime": runtime.model_dump(mode="json")},
        )

    async def costmap_layers(
        self,
        runtime: RuntimeBinding,
    ) -> tuple[CostmapLayerEvidence, ...]:
        payload = await self.transport.observe(
            IsaacObservationOperation.COSTMAP_LAYERS,
            {"runtime": runtime.model_dump(mode="json")},
        )
        if not isinstance(payload, list):
            raise ValueError("Isaac observation probe costmap response must be a list")
        return tuple(CostmapLayerEvidence.model_validate(item) for item in payload)

    async def collision_timeline(
        self,
        runtime: RuntimeBinding,
    ) -> CollisionStreamEvidence:
        return await self._model(
            IsaacObservationOperation.COLLISION_TIMELINE,
            CollisionStreamEvidence,
            {"runtime": runtime.model_dump(mode="json")},
        )

    async def clearance_budget(self, runtime: RuntimeBinding) -> ClearanceBudget:
        return await self._model(
            IsaacObservationOperation.CLEARANCE_BUDGET,
            ClearanceBudget,
            {"runtime": runtime.model_dump(mode="json")},
        )

    async def clearance_sources(
        self,
        runtime: RuntimeBinding,
    ) -> tuple[ClearanceSourceEvidence, ...]:
        payload = await self.transport.observe(
            IsaacObservationOperation.CLEARANCE_SOURCES,
            {"runtime": runtime.model_dump(mode="json")},
        )
        if not isinstance(payload, list):
            raise ValueError("Isaac observation probe clearance response must be a list")
        return tuple(ClearanceSourceEvidence.model_validate(item) for item in payload)
