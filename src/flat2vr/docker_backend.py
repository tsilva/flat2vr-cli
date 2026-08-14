"""Run flat2vr in a local, API-addressed, or SSH-addressed Docker daemon."""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from flat2vr.options import ConversionOptions
from flat2vr.process import CommandError, display_command, run
from flat2vr.resources import container_context


DEFAULT_IMAGE = "flat2vr:latest"
DEFAULT_MODEL_VOLUME = "flat2vr-models"


class DockerBackend:
    def __init__(
        self,
        *,
        docker_host: str | None = None,
        docker_ssh: str | None = None,
        docker_sudo: bool = False,
        image: str = DEFAULT_IMAGE,
        model_volume: str = DEFAULT_MODEL_VOLUME,
        model_path: str | None = None,
    ) -> None:
        if docker_host and docker_ssh:
            raise ValueError("use either docker_host or docker_ssh, not both")
        if model_path and "," in model_path:
            raise ValueError("Docker bind paths containing commas are unsupported")
        self.docker_host = docker_host
        self.docker_ssh = docker_ssh
        self.docker_sudo = docker_sudo
        self.image = image
        self.model_volume = model_volume
        self.model_path = model_path

    def command(self, *arguments: str) -> list[str]:
        if self.docker_ssh:
            command = [
                "ssh",
                "-o",
                "BatchMode=yes",
                self.docker_ssh,
            ]
            remote = []
            if self.docker_sudo:
                remote.extend(["sudo", "-n"])
            remote.append("docker")
            remote.extend(arguments)
            command.append(shlex.join(remote))
            return command

        command = ["docker"]
        if self.docker_host:
            command.extend(["--host", self.docker_host])
        command.extend(arguments)
        return command

    def doctor(self) -> None:
        result = run(
            self.command(
                "version",
                "--format",
                "client={{.Client.Version}} server={{.Server.Version}}",
            ),
            capture_output=True,
        )
        print(result.stdout.strip())
        info = run(
            self.command(
                "info",
                "--format",
                "runtimes={{range $name, $_ := .Runtimes}}"
                "{{$name}} {{end}}default={{.DefaultRuntime}}",
            ),
            capture_output=True,
        )
        print(info.stdout.strip())

    def image_exists(self) -> bool:
        result = run(
            self.command("image", "inspect", self.image),
            capture_output=True,
            check=False,
        )
        return result.returncode == 0

    def build(self, *, rebuild: bool = False) -> None:
        context = container_context()
        with tempfile.TemporaryFile() as archive_file:
            # Legacy Docker builders do not recognize POSIX PAX headers as a
            # streamed build context. GNU tar is understood by both builders.
            with tarfile.open(
                fileobj=archive_file,
                mode="w",
                format=tarfile.GNU_FORMAT,
            ) as archive:
                for path in sorted(context.rglob("*")):
                    relative = path.relative_to(context)
                    if _ignored(relative):
                        continue
                    archive.add(path, arcname=relative.as_posix(), recursive=False)
            archive_file.seek(0)
            # Keep this compatible with minimal GPU hosts that have Docker's
            # legacy builder but not the optional buildx plugin.
            arguments = ["build", "-t", self.image]
            if rebuild:
                arguments.append("--no-cache")
            arguments.append("-")
            run(self.command(*arguments), stdin=archive_file)

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        options: ConversionOptions,
        *,
        rebuild: bool = False,
        keep_container: bool = False,
    ) -> None:
        options.validate()
        input_path = input_path.expanduser().resolve()
        output_path = output_path.expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"input video does not exist: {input_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if rebuild or not self.image_exists():
            self.build(rebuild=rebuild)

        if not self.model_path:
            run(self.command("volume", "create", self.model_volume), capture_output=True)

        job_id = uuid.uuid4().hex
        container_name = f"flat2vr-{job_id}"
        suffix = input_path.suffix.lower()
        if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
            suffix = ".mp4"
        container_input = f"/work/input/source{suffix}"
        container_output = "/work/output/result_Full_SBS.mp4"

        if self.model_path:
            model_mount = f"type=bind,src={self.model_path},dst=/models"
        else:
            model_mount = f"type=volume,src={self.model_volume},dst=/models"

        create = self.command(
            "create",
            "--name",
            container_name,
            "--gpus",
            "all",
            "--shm-size",
            "8g",
            "--mount",
            model_mount,
            "--env",
            "FLAT2VR_MODEL_DIR=/models",
            "--env",
            "HF_HOME=/models/huggingface",
            "--env",
            "XDG_CACHE_HOME=/models/cache",
            "--env",
            "NVIDIA_DRIVER_CAPABILITIES=compute,utility,video",
            self.image,
            "/opt/flat2vr/bin/convert",
            container_input,
            container_output,
            *options.container_args(),
        )

        created = False
        try:
            run(create, capture_output=True)
            created = True
            self._copy_input(input_path, container_name, suffix)
            run(self.command("start", "--attach", container_name))
            self._copy_output(container_name, container_output, output_path)
        finally:
            if created and not keep_container:
                run(
                    self.command("rm", "--force", container_name),
                    capture_output=True,
                    check=False,
                )

    def _copy_input(self, source: Path, container_name: str, suffix: str) -> None:
        command = self.command("cp", "-", f"{container_name}:/work")
        print(f"+ {display_command(command)} < {source}", flush=True)
        process = subprocess.Popen(command, stdin=subprocess.PIPE)
        assert process.stdin is not None
        try:
            with tarfile.open(
                fileobj=process.stdin,
                mode="w|",
                format=tarfile.GNU_FORMAT,
            ) as archive:
                archive.add(source, arcname=f"input/source{suffix}", recursive=False)
        finally:
            process.stdin.close()
        return_code = process.wait()
        if return_code:
            raise CommandError(f"docker cp upload failed with exit code {return_code}")

    def _copy_output(
        self,
        container_name: str,
        container_output: str,
        destination: Path,
    ) -> None:
        command = self.command("cp", f"{container_name}:{container_output}", "-")
        print(f"+ {display_command(command)} > {destination}", flush=True)
        process = subprocess.Popen(command, stdout=subprocess.PIPE)
        assert process.stdout is not None

        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
        found = False
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|*") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    source = archive.extractfile(member)
                    if source is None:
                        continue
                    with temporary.open("wb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    found = True
                    break
            return_code = process.wait()
            if return_code:
                raise CommandError(f"docker cp download failed with exit code {return_code}")
            if not found:
                raise CommandError("docker cp returned no output file")
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()


def _ignored(relative: Path) -> bool:
    ignored_parts = {".git", ".venv", "__pycache__", ".pytest_cache"}
    return any(part in ignored_parts for part in relative.parts)
