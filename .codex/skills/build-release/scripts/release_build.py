#!/usr/bin/env python3
"""Prepare, build, audit, smoke-test, and verify flat2vr-cli releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
import zipfile
from email.parser import BytesParser
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[4]
PACKAGE_NAME = "flat2vr-cli"
PACKAGE_FILENAME = "flat2vr_cli"
IMPORT_NAME = "flat2vr"
ENTRY_POINT = "flat2vr = flat2vr.cli:main"
VERSION_PATTERN = re.compile(
    r"^(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<patch>[0-9]+)"
    r"(?:(?P<pre>a|b|rc)(?P<pre_number>[0-9]+)"
    r"|\.post(?P<post_number>[0-9]+)"
    r"|\.dev(?P<dev_number>[0-9]+))?$"
)
VERSION_FILES = (
    REPO_ROOT / "pyproject.toml",
    REPO_ROOT / "src" / IMPORT_NAME / "__init__.py",
    REPO_ROOT / "uv.lock",
)
CONTAMINATION_PARTS = {
    ".env",
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
}
REQUIRED_WHEEL_FILES = {
    "flat2vr/__init__.py",
    "flat2vr/__main__.py",
    "flat2vr/cli.py",
    "flat2vr/docker_backend.py",
    "flat2vr/modal_backend.py",
    "flat2vr/options.py",
    "flat2vr/resources.py",
    "flat2vr/container/Dockerfile",
    "flat2vr/container/bin/convert",
    "flat2vr/container/locks/depthcrafter/uv.lock",
    "flat2vr/container/locks/sgm/uv.lock",
    "flat2vr/container/patches/m2svid-inference.patch",
    "flat2vr/container/tools/convert.py",
    "flat2vr/container/tools/depth_and_warp.py",
    "flat2vr/container/tools/ensure_models.py",
    "flat2vr/container/tools/inpaint_batch.py",
}


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    print("+", shlex.join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def read_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def project_metadata() -> tuple[str, str]:
    project = read_toml(REPO_ROOT / "pyproject.toml").get("project")
    if not isinstance(project, dict):
        raise SystemExit("pyproject.toml is missing [project]")
    name = project.get("name")
    version = project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise SystemExit("project name and version must be strings")
    return name, version


def import_version() -> str:
    path = REPO_ROOT / "src" / IMPORT_NAME / "__init__.py"
    match = re.search(
        r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
        path.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"could not find __version__ in {path}")
    return match.group(1)


def lock_version() -> str:
    packages = read_toml(REPO_ROOT / "uv.lock").get("package", [])
    if not isinstance(packages, list):
        raise SystemExit("uv.lock has an invalid package table")
    matches = [
        package.get("version")
        for package in packages
        if isinstance(package, dict) and package.get("name") == PACKAGE_NAME
    ]
    if len(matches) != 1 or not isinstance(matches[0], str):
        raise SystemExit(f"expected one {PACKAGE_NAME!r} package in uv.lock")
    return matches[0]


def validate_version(version: str) -> None:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise SystemExit(f"unsupported PEP 440 release version: {version!r}")


def check_version(version: str | None = None) -> str:
    project_name, project_version = project_metadata()
    expected = version or project_version
    validate_version(expected)
    actual = {
        "project.name": project_name,
        "pyproject.toml": project_version,
        "src/flat2vr/__init__.py": import_version(),
        "uv.lock": lock_version(),
    }
    wanted = {
        "project.name": PACKAGE_NAME,
        "pyproject.toml": expected,
        "src/flat2vr/__init__.py": expected,
        "uv.lock": expected,
    }
    failures = {key: value for key, value in actual.items() if value != wanted[key]}
    if failures:
        details = ", ".join(
            f"{key}={value!r}, expected {wanted[key]!r}"
            for key, value in failures.items()
        )
        raise SystemExit(f"release metadata mismatch for {expected}: {details}")
    print(json.dumps({"package": PACKAGE_NAME, "version": expected}, indent=2))
    return expected


def fetch_pypi() -> dict[str, object]:
    request = urllib.request.Request(
        f"https://pypi.org/pypi/{PACKAGE_NAME}/json",
        headers={
            "Accept": "application/json",
            "User-Agent": "flat2vr-cli-release-check",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return {}
        raise
    if not isinstance(payload, dict):
        raise SystemExit("unexpected PyPI JSON response")
    return payload


def pypi_releases() -> dict[str, object]:
    releases = fetch_pypi().get("releases", {})
    if not isinstance(releases, dict):
        raise SystemExit("unexpected PyPI releases payload")
    return releases


def tagged_versions() -> set[str]:
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        tag.removeprefix("v")
        for tag in result.stdout.splitlines()
        if tag.startswith("v")
    }


def next_version(version: str) -> str:
    match = VERSION_PATTERN.fullmatch(version)
    if match is None:
        raise SystemExit(f"unsupported PEP 440 release version: {version!r}")
    base = ".".join(match.group(name) for name in ("major", "minor", "patch"))
    if pre := match.group("pre"):
        return f"{base}{pre}{int(match.group('pre_number')) + 1}"
    if dev := match.group("dev_number"):
        return f"{base}.dev{int(dev) + 1}"
    return (
        f"{match.group('major')}.{match.group('minor')}."
        f"{int(match.group('patch')) + 1}"
    )


def select_release_version(
    current: str,
    releases: dict[str, object],
    tags: set[str],
) -> str:
    candidate = current
    while releases.get(candidate) or candidate in tags:
        candidate = next_version(candidate)
    return candidate


def replace_project_version(path: Path, current: str, target: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'(?ms)(^\[project\]\n.*?^version\s*=\s*"){re.escape(current)}(")'
    )
    updated, count = pattern.subn(rf"\g<1>{target}\g<2>", text, count=1)
    if count != 1:
        raise SystemExit(f"could not update [project] version in {path}")
    path.write_text(updated, encoding="utf-8")


def replace_import_version(path: Path, current: str, target: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'(?m)^(?P<prefix>__version__\s*=\s*["\']){re.escape(current)}'
        rf'(?P<suffix>["\'])$'
    )
    updated, count = pattern.subn(
        rf"\g<prefix>{target}\g<suffix>",
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"could not update __version__ in {path}")
    path.write_text(updated, encoding="utf-8")


def replace_lock_version(path: Path, current: str, target: str) -> None:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'(?m)(^\[\[package\]\]\nname = "{PACKAGE_NAME}"\nversion = ")'
        rf'{re.escape(current)}(")'
    )
    updated, count = pattern.subn(rf"\g<1>{target}\g<2>", text, count=1)
    if count != 1:
        raise SystemExit(f"could not update {PACKAGE_NAME!r} version in {path}")
    path.write_text(updated, encoding="utf-8")


def write_version(target: str) -> None:
    validate_version(target)
    _, current = project_metadata()
    check_version(current)
    snapshots = {path: path.read_bytes() for path in VERSION_FILES}
    try:
        replace_project_version(VERSION_FILES[0], current, target)
        replace_import_version(VERSION_FILES[1], current, target)
        replace_lock_version(VERSION_FILES[2], current, target)
        check_version(target)
    except BaseException:
        for path, contents in snapshots.items():
            path.write_bytes(contents)
        raise


def prepare_version(target: str | None, write: bool) -> None:
    _, current = project_metadata()
    check_version(current)
    releases = pypi_releases()
    tags = tagged_versions()
    if target:
        validate_version(target)
        if releases.get(target):
            raise SystemExit(f"{PACKAGE_NAME}=={target} already exists on PyPI")
        if target in tags:
            raise SystemExit(f"release tag already exists: v{target}")
        selected = target
    else:
        selected = select_release_version(current, releases, tags)
    if write and selected != current:
        write_version(selected)
    print(
        json.dumps(
            {
                "package": PACKAGE_NAME,
                "current_version": current,
                "selected_version": selected,
                "bumped": selected != current,
                "written": bool(write and selected != current),
            },
            indent=2,
        )
    )


def check_pypi(version: str) -> None:
    validate_version(version)
    files = pypi_releases().get(version, [])
    if files:
        names = sorted(str(item.get("filename")) for item in files)
        raise SystemExit(f"PyPI {PACKAGE_NAME} {version} already has files: {names}")
    print(json.dumps({"package": PACKAGE_NAME, "version": version, "unused": True}))


def source_gates() -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required for release source gates")
    with tempfile.TemporaryDirectory(prefix="flat2vr-release-uv-config-") as config:
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = config
        for command in (
            [uv, "lock", "--check"],
            [uv, "sync", "--frozen"],
            [uv, "run", "--frozen", "python", "-m", "compileall", "-q", "src"],
            [
                uv,
                "run",
                "--frozen",
                "python",
                "-m",
                "unittest",
                "discover",
                "-s",
                "tests",
                "-v",
            ],
            [uv, "run", "--frozen", "flat2vr", "--help"],
        ):
            run(command, cwd=REPO_ROOT, env=env)


def contaminated(member_name: str) -> bool:
    parts = set(PurePosixPath(member_name).parts)
    return bool(parts & CONTAMINATION_PARTS) or member_name.endswith((".pyc", ".pyo"))


def expected_names(version: str) -> tuple[str, str]:
    return (
        f"{PACKAGE_FILENAME}-{version}-py3-none-any.whl",
        f"{PACKAGE_FILENAME}-{version}.tar.gz",
    )


def audit_wheel(path: Path, version: str) -> dict[str, object]:
    expected_wheel, _ = expected_names(version)
    if path.name != expected_wheel:
        raise SystemExit(f"expected wheel {expected_wheel}, got {path.name}")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        wheel_names = [name for name in names if name.endswith(".dist-info/WHEEL")]
        entry_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(metadata_names) != 1 or len(wheel_names) != 1 or len(entry_names) != 1:
            raise SystemExit("wheel must contain one METADATA, WHEEL, and entry_points.txt")
        metadata = BytesParser().parsebytes(archive.read(metadata_names[0]))
        wheel_metadata = BytesParser().parsebytes(archive.read(wheel_names[0]))
        entry_points = archive.read(entry_names[0]).decode("utf-8")
    checks = {
        "metadata_name": metadata.get("Name") == PACKAGE_NAME,
        "metadata_version": metadata.get("Version") == version,
        "universal_wheel": wheel_metadata.get("Tag") == "py3-none-any",
        "console_script": ENTRY_POINT in entry_points,
        "required_package_files": REQUIRED_WHEEL_FILES.issubset(names),
        "no_generated_or_private_paths": not any(contaminated(name) for name in names),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print(json.dumps({"wheel": str(path), "checks": checks}, indent=2))
        raise SystemExit(f"wheel audit failed: {failed}")
    return {"wheel": str(path), "checks": checks}


def audit_sdist(path: Path, version: str) -> dict[str, object]:
    _, expected_sdist = expected_names(version)
    if path.name != expected_sdist:
        raise SystemExit(f"expected sdist {expected_sdist}, got {path.name}")
    prefix = f"{PACKAGE_FILENAME}-{version}/"
    with tarfile.open(path, "r:gz") as archive:
        names = [member.name for member in archive.getmembers() if member.isfile()]
    required = {
        f"{prefix}pyproject.toml",
        f"{prefix}README.md",
        f"{prefix}src/flat2vr/__init__.py",
        f"{prefix}src/flat2vr/container/Dockerfile",
        f"{prefix}src/flat2vr/container/bin/convert",
        f"{prefix}src/flat2vr/container/locks/depthcrafter/uv.lock",
        f"{prefix}src/flat2vr/container/locks/sgm/uv.lock",
    }
    checks = {
        "single_root": bool(names) and all(name.startswith(prefix) for name in names),
        "required_source_files": required.issubset(names),
        "no_generated_or_private_paths": not any(contaminated(name) for name in names),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        print(json.dumps({"sdist": str(path), "checks": checks}, indent=2))
        raise SystemExit(f"sdist audit failed: {failed}")
    return {"sdist": str(path), "checks": checks}


def distribution_paths(directory: Path, version: str) -> tuple[Path, Path]:
    wheel_name, sdist_name = expected_names(version)
    wheel = directory / wheel_name
    sdist = directory / sdist_name
    missing = [str(path) for path in (wheel, sdist) if not path.is_file()]
    if missing:
        raise SystemExit(f"missing release distributions: {missing}")
    unexpected = sorted(
        path.name for path in directory.iterdir() if path.is_file() and path not in {wheel, sdist}
    )
    if unexpected:
        raise SystemExit(f"release directory contains unexpected files: {unexpected}")
    return wheel, sdist


def smoke_wheel(wheel: Path, version: str) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required for wheel smoke testing")
    with tempfile.TemporaryDirectory(prefix="flat2vr-release-smoke-") as temporary:
        env_dir = Path(temporary) / "venv"
        run([uv, "venv", "--python", sys.executable, str(env_dir)])
        python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        run([uv, "pip", "install", "--python", str(python), "--no-deps", str(wheel)])
        code = """
import importlib.metadata as metadata
import flat2vr
from flat2vr.cli import build_parser
from flat2vr.resources import container_context

assert flat2vr.__version__ == metadata.version("flat2vr-cli") == __import__("sys").argv[1]
entry_points = [
    entry
    for entry in metadata.entry_points(group="console_scripts")
    if entry.name == "flat2vr"
]
assert len(entry_points) == 1 and entry_points[0].value == "flat2vr.cli:main"
assert build_parser().parse_args(["convert", "input.mp4"]).backend == "docker"
context = container_context()
for relative in (
    "Dockerfile",
    "bin/convert",
    "locks/depthcrafter/uv.lock",
    "locks/sgm/uv.lock",
    "tools/convert.py",
):
    assert (context / relative).is_file(), relative
"""
        run([str(python), "-c", code, version], cwd=Path(temporary))
        script = env_dir / ("Scripts/flat2vr.exe" if os.name == "nt" else "bin/flat2vr")
        run([str(script), "--help"], cwd=Path(temporary))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_directory(directory: Path, version: str) -> tuple[Path, Path]:
    wheel, sdist = distribution_paths(directory, version)
    result = {
        "version": version,
        "audits": [audit_wheel(wheel, version), audit_sdist(sdist, version)],
        "sha256": {wheel.name: sha256(wheel), sdist.name: sha256(sdist)},
    }
    smoke_wheel(wheel, version)
    print(json.dumps(result, indent=2, sort_keys=True))
    return wheel, sdist


def build(version: str, out_dir: Path) -> None:
    check_version(version)
    output = out_dir.resolve()
    if output.exists():
        raise SystemExit(f"release output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "uv",
            "build",
            "--no-sources",
            "--no-create-gitignore",
            "--out-dir",
            str(output),
        ],
        cwd=REPO_ROOT,
    )
    audit_directory(output, version)


def wait_pypi(version: str, attempts: int, interval: float) -> None:
    validate_version(version)
    expected = set(expected_names(version))
    for attempt in range(1, attempts + 1):
        files = pypi_releases().get(version, [])
        names = {str(item.get("filename")) for item in files}
        if expected.issubset(names):
            print(
                json.dumps(
                    {
                        "url": f"https://pypi.org/project/{PACKAGE_NAME}/{version}/",
                        "files": sorted(names),
                    },
                    indent=2,
                )
            )
            return
        print(f"waiting for {PACKAGE_NAME} {version} ({attempt}/{attempts})", flush=True)
        if attempt < attempts:
            time.sleep(interval)
    raise SystemExit(f"{PACKAGE_NAME} {version} did not appear on PyPI with both files")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    version = commands.add_parser("check-version")
    version.add_argument("--version")

    prepare = commands.add_parser("prepare-version")
    prepare.add_argument("--to")
    prepare.add_argument("--write", action="store_true")

    pypi = commands.add_parser("check-pypi")
    pypi.add_argument("--version", required=True)

    commands.add_parser("source-gates")

    candidate = commands.add_parser("build")
    candidate.add_argument("--version", required=True)
    candidate.add_argument("--out-dir", type=Path, required=True)

    audit = commands.add_parser("audit")
    audit.add_argument("--version", required=True)
    audit.add_argument("--dist-dir", type=Path, required=True)

    wait = commands.add_parser("wait-pypi")
    wait.add_argument("--version", required=True)
    wait.add_argument("--attempts", type=int, default=60)
    wait.add_argument("--interval", type=float, default=10.0)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "check-version":
        check_version(args.version)
    elif args.command == "prepare-version":
        prepare_version(args.to, args.write)
    elif args.command == "check-pypi":
        check_pypi(args.version)
    elif args.command == "source-gates":
        source_gates()
    elif args.command == "build":
        build(args.version, args.out_dir if args.out_dir.is_absolute() else REPO_ROOT / args.out_dir)
    elif args.command == "audit":
        check_version(args.version)
        audit_directory(args.dist_dir.resolve(), args.version)
    elif args.command == "wait-pypi":
        wait_pypi(args.version, args.attempts, args.interval)
    else:
        raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    main()
