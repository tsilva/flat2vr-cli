"""Modal deployment definition used by ``flat2vr modal deploy``."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

import modal

from flat2vr.options import ConversionOptions
from flat2vr.resources import container_context


APP_NAME = os.environ.get("FLAT2VR_MODAL_APP", "flat2vr")
MODEL_VOLUME_NAME = os.environ.get(
    "FLAT2VR_MODAL_MODEL_VOLUME", "flat2vr-models"
)
JOBS_VOLUME_NAME = os.environ.get("FLAT2VR_MODAL_JOBS_VOLUME", "flat2vr-jobs")
GPU = os.environ.get("FLAT2VR_MODAL_GPU", "L40S")

app = modal.App(APP_NAME)
image = modal.Image.from_dockerfile(
    container_context() / "Dockerfile",
    context_dir=container_context(),
    add_python="3.11",
)
models = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)
jobs = modal.Volume.from_name(JOBS_VOLUME_NAME, create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    timeout=6 * 60 * 60,
    volumes={"/models": models, "/jobs": jobs},
)
def convert(job_id: str, raw_options: dict[str, object]) -> dict[str, object]:
    parsed = uuid.UUID(job_id)
    if parsed.hex != job_id:
        raise ValueError("invalid job id")
    options = ConversionOptions.from_dict(raw_options)

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
    subprocess.run(command, check=True, env=environment)
    models.commit()
    jobs.commit()
    return {
        "output": str(output.relative_to("/jobs")),
        "size": output.stat().st_size,
    }
