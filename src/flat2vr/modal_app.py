"""Modal deployment definition managed by ``flat2vr setup``."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

import modal

from flat2vr.modal_contract import (
    APP_NAME,
    DEFAULT_GPU,
    JOBS_VOLUME,
    MODEL_VOLUME,
    deployment_tags,
)
from flat2vr.options import ConversionOptions
from flat2vr.resources import container_context


GPU = os.environ.get("FLAT2VR_MODAL_GPU", DEFAULT_GPU)

app = modal.App(APP_NAME, tags=deployment_tags(gpu=GPU))
image = modal.Image.from_dockerfile(
    container_context() / "Dockerfile",
    context_dir=container_context(),
    add_python="3.11",
)
models = modal.Volume.from_name(MODEL_VOLUME, create_if_missing=True)
jobs = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    timeout=6 * 60 * 60,
    volumes={"/models": models, "/jobs": jobs},
)
def convert(job_id: str, request: dict[str, object]) -> dict[str, object]:
    parsed = uuid.UUID(job_id)
    if parsed.hex != job_id:
        raise ValueError("invalid job id")
    options, verbose, keep_work = ConversionOptions.from_request(request)

    job_root = Path("/jobs") / job_id
    inputs = [path for path in (job_root / "input").iterdir() if path.is_file()]
    if len(inputs) != 1:
        raise RuntimeError(f"expected one input file, found {len(inputs)}")
    output = job_root / "output" / "result_Full_SBS.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    environment.update(
        {
            "FLAT2VR_MODEL_DIR": "/models",
            "FLAT2VR_WORK_DIR": str(job_root / "work"),
            "HF_HOME": "/models/huggingface",
            "XDG_CACHE_HOME": "/models/cache",
        }
    )
    command = [
        "/opt/flat2vr/bin/convert",
        str(inputs[0]),
        str(output),
        *options.container_args(),
    ]
    if keep_work:
        command.append("--keep-work")
    if verbose:
        command.append("--verbose")
    subprocess.run(command, check=True, env=environment)
    models.commit()
    jobs.commit()
    return {
        "output": str(output.relative_to("/jobs")),
        "size": output.stat().st_size,
    }
