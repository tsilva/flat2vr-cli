"""Small, credential-free persistent configuration for the CLI."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

from flat2vr.modal_contract import DEFAULT_GPU


CONFIG_VERSION = 1
DOCKER_HOST_SCHEMES = ("tcp://", "unix://", "npipe://", "http://", "https://")


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DockerConfiguration:
    target: str | None = None
    sudo: bool = False
    model_path: str | None = None

    def validate(self) -> None:
        if self.target is not None:
            if not isinstance(self.target, str) or not self.target:
                raise ConfigurationError("Docker target must be a non-empty string")
            if self.target.startswith("ssh://"):
                parsed = urlsplit(self.target)
                if not parsed.netloc:
                    raise ConfigurationError("Docker SSH target is missing a host")
                if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
                    raise ConfigurationError(
                        "Docker SSH target must contain only a user and host"
                    )
            elif not self.target.startswith(DOCKER_HOST_SCHEMES):
                raise ConfigurationError(
                    "Docker target must start with ssh://, tcp://, unix://, "
                    "npipe://, http://, or https://"
                )
        if not isinstance(self.sudo, bool):
            raise ConfigurationError("Docker sudo setting must be a boolean")
        if self.sudo and not (self.target or "").startswith("ssh://"):
            raise ConfigurationError("Docker sudo is supported only for ssh:// targets")
        if self.model_path is not None:
            if not isinstance(self.model_path, str) or not self.model_path:
                raise ConfigurationError("Docker model path must be a non-empty string")
            if "," in self.model_path:
                raise ConfigurationError("Docker model path cannot contain commas")


@dataclass(frozen=True, slots=True)
class Configuration:
    backend: str = "modal"
    modal_gpu: str = DEFAULT_GPU
    docker: DockerConfiguration = DockerConfiguration()

    def validate(self) -> None:
        if self.backend not in ("modal", "docker"):
            raise ConfigurationError("backend must be modal or docker")
        if not isinstance(self.modal_gpu, str) or not self.modal_gpu.strip():
            raise ConfigurationError("Modal GPU must be a non-empty string")
        self.docker.validate()

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return {
            "version": CONFIG_VERSION,
            "backend": self.backend,
            "modal": {"gpu": self.modal_gpu},
            "docker": {
                "target": self.docker.target,
                "sudo": self.docker.sudo,
                "model_path": self.docker.model_path,
            },
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> "Configuration":
        if set(value) != {"version", "backend", "modal", "docker"}:
            raise ConfigurationError("configuration has missing or unknown fields")
        if value.get("version") != CONFIG_VERSION:
            raise ConfigurationError(
                f"unsupported configuration version: {value.get('version')!r}"
            )
        modal = value.get("modal")
        docker = value.get("docker")
        if not isinstance(modal, dict) or set(modal) != {"gpu"}:
            raise ConfigurationError("configuration modal section is invalid")
        if not isinstance(docker, dict) or set(docker) != {
            "target",
            "sudo",
            "model_path",
        }:
            raise ConfigurationError("configuration docker section is invalid")
        result = cls(
            backend=value.get("backend"),  # type: ignore[arg-type]
            modal_gpu=modal.get("gpu"),  # type: ignore[arg-type]
            docker=DockerConfiguration(
                target=docker.get("target"),  # type: ignore[arg-type]
                sudo=docker.get("sudo"),  # type: ignore[arg-type]
                model_path=docker.get("model_path"),  # type: ignore[arg-type]
            ),
        )
        result.validate()
        return result


def default_config_path() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / "flat2vr" / "config.json"


def load_configuration(path: Path | None = None) -> Configuration | None:
    selected = path or default_config_path()
    if not selected.exists():
        return None
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            f"could not read {selected}; run `flat2vr setup` after fixing or "
            "removing it"
        ) from error
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration in {selected} must be an object")
    try:
        return Configuration.from_dict(value)
    except ConfigurationError as error:
        raise ConfigurationError(
            f"invalid configuration in {selected}: {error}; run `flat2vr setup` "
            "after fixing or removing it"
        ) from error


def save_configuration(
    configuration: Configuration,
    path: Path | None = None,
) -> Path:
    selected = path or default_config_path()
    selected.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(configuration.to_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{selected.name}.",
        suffix=".tmp",
        dir=selected.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, selected)
    finally:
        temporary.unlink(missing_ok=True)
    return selected
