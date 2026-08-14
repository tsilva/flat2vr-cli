#!/usr/bin/env python3
"""Populate and verify the persistent flat2vr model cache."""

from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import urllib.request
import zipfile

from huggingface_hub import snapshot_download


MODEL_ROOT = Path(os.environ.get("FLAT2VR_MODEL_DIR", "/models"))
DEPTH_REVISION = "f2207fa67e799d3cac41b0a051bb051401137a5b"
SVD_REVISION = "9e43909513c6714f1bc78bcb44d96e733cd242aa"
M2SVID_ZIP = "https://storage.googleapis.com/gresearch/m2svid/m2svid_weights.zip"
EXPECTED = {
    "depthcrafter/diffusion_pytorch_model.safetensors": "48feba12ce91f2ba2e7d3853bfcdd50ad45a4980be227f43f5add28baa451f94",
    "svd-xt/image_encoder/model.fp16.safetensors": "ae616c24393dd1854372b0639e5541666f7521cbe219669255e865cb7f89466a",
    "svd-xt/vae/diffusion_pytorch_model.fp16.safetensors": "af602cd0eb4ad6086ec94fbf1438dfb1be5ec9ac03fd0215640854e90d6463a3",
    "m2svid/m2svid_weights.pt": "c4c03852bfda7f0823229126a6c7818371135dc87e01995e0551546936efdbbd",
    "m2svid/open_clip_pytorch_model.bin": "9a78ef8e8c73fd0df621682e7a8e8eb36c6916cb3c16b291a082ecd52ab79cc4",
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def verified(relative: str) -> bool:
    path = MODEL_ROOT / relative
    return path.is_file() and digest(path) == EXPECTED[relative]


def download(url: str, destination: Path) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.unlink(missing_ok=True)
    print(f"Downloading {url}...", flush=True)
    try:
        with urllib.request.urlopen(url) as source, temporary.open("wb") as target:
            shutil.copyfileobj(source, target, length=8 * 1024 * 1024)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def ensure_huggingface_models() -> None:
    if not verified("depthcrafter/diffusion_pytorch_model.safetensors"):
        snapshot_download(
            repo_id="tencent/DepthCrafter",
            revision=DEPTH_REVISION,
            local_dir=MODEL_ROOT / "depthcrafter",
            allow_patterns=["config.json", "diffusion_pytorch_model.safetensors"],
        )
    if not (
        verified("svd-xt/image_encoder/model.fp16.safetensors")
        and verified("svd-xt/vae/diffusion_pytorch_model.fp16.safetensors")
    ):
        snapshot_download(
            repo_id="stabilityai/stable-video-diffusion-img2vid-xt",
            revision=SVD_REVISION,
            local_dir=MODEL_ROOT / "svd-xt",
            allow_patterns=[
                "model_index.json",
                "feature_extractor/*",
                "image_encoder/config.json",
                "image_encoder/model.fp16.safetensors",
                "scheduler/*",
                "unet/config.json",
                "vae/config.json",
                "vae/diffusion_pytorch_model.fp16.safetensors",
            ],
        )


def ensure_m2svid_models() -> None:
    names = ("m2svid_weights.pt", "open_clip_pytorch_model.bin")
    if all(verified(f"m2svid/{name}") for name in names):
        return
    archive = MODEL_ROOT / "m2svid_weights.zip"
    download(M2SVID_ZIP, archive)
    target = MODEL_ROOT / "m2svid"
    target.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            for wanted in names:
                candidates = [
                    name
                    for name in bundle.namelist()
                    if Path(name).name == wanted
                ]
                if len(candidates) != 1:
                    raise RuntimeError(f"expected one {wanted} in model archive")
                temporary = target / f".{wanted}.part"
                with bundle.open(candidates[0]) as source, temporary.open("wb") as output:
                    shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
                os.replace(temporary, target / wanted)
    finally:
        archive.unlink(missing_ok=True)


def main() -> None:
    MODEL_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = MODEL_ROOT / ".flat2vr-models.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ensure_huggingface_models()
        ensure_m2svid_models()
        failures = [relative for relative in EXPECTED if not verified(relative)]
        if failures:
            raise RuntimeError("model integrity check failed: " + ", ".join(failures))
        (MODEL_ROOT / ".flat2vr-models-ready").write_text(
            f"depth={DEPTH_REVISION}\nsvd={SVD_REVISION}\n", encoding="utf-8"
        )
    print(f"Models ready in {MODEL_ROOT}", flush=True)


if __name__ == "__main__":
    main()
