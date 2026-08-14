"""Dependency-free constants shared by the Modal client and deployment."""

from __future__ import annotations

from flat2vr import __version__
from flat2vr.options import PROTOCOL_VERSION


APP_NAME = "flat2vr"
MODEL_VOLUME = "flat2vr-models"
JOBS_VOLUME = "flat2vr-jobs"
DEFAULT_GPU = "L40S"
VERSION_TAG = "flat2vr-version"
PROTOCOL_TAG = "flat2vr-protocol"
GPU_TAG = "flat2vr-gpu"


def deployment_tags(*, gpu: str = DEFAULT_GPU) -> dict[str, str]:
    return {
        VERSION_TAG: __version__,
        PROTOCOL_TAG: str(PROTOCOL_VERSION),
        GPU_TAG: gpu,
    }
