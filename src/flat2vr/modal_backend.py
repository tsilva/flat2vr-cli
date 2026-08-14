"""Modal client backend."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import uuid

from flat2vr.options import ConversionOptions


DEFAULT_APP = "flat2vr"
DEFAULT_MODEL_VOLUME = "flat2vr-models"
DEFAULT_JOBS_VOLUME = "flat2vr-jobs"
DEFAULT_GPU = "L40S"


def _modal():
    try:
        import modal
    except ImportError as error:
        raise RuntimeError(
            "Modal support is not installed. Run: uv sync --extra modal"
        ) from error
    return modal


class ModalBackend:
    def __init__(
        self,
        *,
        app_name: str = DEFAULT_APP,
        model_volume: str = DEFAULT_MODEL_VOLUME,
        jobs_volume: str = DEFAULT_JOBS_VOLUME,
    ) -> None:
        self.app_name = app_name
        self.model_volume = model_volume
        self.jobs_volume = jobs_volume

    def doctor(self) -> None:
        modal = _modal()
        volume = modal.Volume.from_name(self.jobs_volume, create_if_missing=True)
        volume.info()
        try:
            modal.Function.from_name(self.app_name, "convert").hydrate()
        except Exception as error:
            raise RuntimeError(
                f"Modal app {self.app_name!r} is not deployed; run "
                "`flat2vr modal deploy`"
            ) from error
        print(
            f"Modal app={self.app_name} jobs={self.jobs_volume} "
            f"models={self.model_volume}"
        )

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        options: ConversionOptions,
    ) -> None:
        modal = _modal()
        options.validate()
        input_path = input_path.expanduser().resolve()
        output_path = output_path.expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"input video does not exist: {input_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        job_id = uuid.uuid4().hex
        suffix = input_path.suffix.lower()
        if not suffix or len(suffix) > 12 or not suffix[1:].isalnum():
            suffix = ".mp4"
        remote_input = f"{job_id}/input/source{suffix}"
        jobs = modal.Volume.from_name(self.jobs_volume, create_if_missing=True)

        print(f"Uploading {input_path.name} to Modal job {job_id}...", flush=True)
        with jobs.batch_upload(force=True) as batch:
            batch.put_file(str(input_path), f"/{remote_input}")

        try:
            function = modal.Function.from_name(self.app_name, "convert")
            print("Running on the deployed Modal GPU...", flush=True)
            with modal.enable_output():
                result = function.remote(job_id, options.to_dict())
            if not isinstance(result, dict) or not isinstance(result.get("output"), str):
                raise RuntimeError(f"unexpected Modal result: {result!r}")

            remote_output = result["output"]
            jobs.reload()
            temporary = output_path.with_name(f".{output_path.name}.{job_id}.part")
            try:
                with temporary.open("wb") as destination:
                    for chunk in jobs.read_file(remote_output):
                        destination.write(chunk)
                os.replace(temporary, output_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        except Exception as error:
            raise RuntimeError(
                f"Modal conversion failed; job files were retained at "
                f"{self.jobs_volume}/{job_id}"
            ) from error
        else:
            jobs.remove_file(job_id, recursive=True)


def deploy_modal(
    *,
    app_name: str = DEFAULT_APP,
    model_volume: str = DEFAULT_MODEL_VOLUME,
    jobs_volume: str = DEFAULT_JOBS_VOLUME,
    gpu: str = DEFAULT_GPU,
) -> None:
    _modal()
    environment = os.environ.copy()
    environment.update(
        {
            "FLAT2VR_MODAL_APP": app_name,
            "FLAT2VR_MODAL_MODEL_VOLUME": model_volume,
            "FLAT2VR_MODAL_JOBS_VOLUME": jobs_volume,
            "FLAT2VR_MODAL_GPU": gpu,
        }
    )
    command = [
        sys.executable,
        "-m",
        "modal",
        "deploy",
        "-m",
        "flat2vr.modal_app",
    ]
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, env=environment)
