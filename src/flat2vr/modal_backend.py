"""Modal authentication, deployment, and conversion client."""

from __future__ import annotations

import importlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import uuid

from flat2vr.modal_contract import APP_NAME, DEFAULT_GPU, JOBS_VOLUME, deployment_tags
from flat2vr.options import ConversionOptions


def _modal():
    try:
        import modal
    except ImportError as error:
        raise RuntimeError(
            "Modal support is unavailable; reinstall with `uv tool install "
            "--force flat2vr-cli`"
        ) from error
    return modal


def _authentication_probe() -> subprocess.CompletedProcess[str]:
    code = "import modal; modal.Client.from_env().hello()"
    return subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )


def ensure_modal_authentication(*, interactive: bool) -> None:
    probe = _authentication_probe()
    if probe.returncode == 0:
        return
    if not interactive:
        raise RuntimeError(
            "Modal is not authenticated; run `flat2vr setup modal` in an "
            "interactive terminal or set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET"
        )
    print("Opening Modal sign-in...", flush=True)
    subprocess.run([sys.executable, "-m", "modal", "setup"], check=True)
    retry = _authentication_probe()
    if retry.returncode:
        detail = (retry.stderr or retry.stdout).strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"Modal authentication did not complete{suffix}")


class ModalBackend:
    def __init__(self, *, gpu: str = DEFAULT_GPU) -> None:
        if not gpu.strip():
            raise ValueError("Modal GPU must be a non-empty string")
        self.gpu = gpu

    def _deployed_tags(self) -> dict[str, str] | None:
        modal = _modal()
        try:
            return modal.App.lookup(APP_NAME).get_tags()
        except modal.exception.NotFoundError:
            return None

    def _function_exists(self) -> bool:
        modal = _modal()
        try:
            modal.Function.from_name(APP_NAME, "convert").hydrate()
        except modal.exception.NotFoundError:
            return False
        return True

    def _deploy(self, *, verbose: bool) -> None:
        modal = _modal()
        os.environ["FLAT2VR_MODAL_GPU"] = self.gpu
        if "flat2vr.modal_app" in sys.modules:
            deployment = importlib.reload(sys.modules["flat2vr.modal_app"])
        else:
            deployment = importlib.import_module("flat2vr.modal_app")
        print(f"Deploying Flat2VR to Modal on {self.gpu}...", flush=True)
        if verbose:
            with modal.enable_output():
                deployment.app.deploy()
        else:
            deployment.app.deploy()

    def setup(self, *, interactive: bool, verbose: bool = False) -> None:
        print("Checking Modal...", flush=True)
        ensure_modal_authentication(interactive=interactive)
        expected = deployment_tags(gpu=self.gpu)
        actual = self._deployed_tags()
        current = actual is not None and all(
            actual.get(key) == value for key, value in expected.items()
        )
        if not current or not self._function_exists():
            self._deploy(verbose=verbose)
            actual = self._deployed_tags()
        verified = actual is not None and all(
            actual.get(key) == value for key, value in expected.items()
        )
        if not verified or not self._function_exists():
            raise RuntimeError("Modal deployment could not be verified")
        print(f"Modal ready: app={APP_NAME} gpu={self.gpu}")

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        options: ConversionOptions,
        *,
        verbose: bool = False,
        keep_work: bool = False,
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
        jobs = modal.Volume.from_name(JOBS_VOLUME, create_if_missing=True)

        primary_error: Exception | None = None
        try:
            print("Uploading input to Modal...", flush=True)
            with jobs.batch_upload(force=True) as batch:
                batch.put_file(str(input_path), f"/{remote_input}")
            function = modal.Function.from_name(APP_NAME, "convert")
            print("Converting on Modal...", flush=True)
            with modal.enable_output():
                result = function.remote(
                    job_id,
                    options.to_request(verbose=verbose, keep_work=keep_work),
                )
            if not isinstance(result, dict) or not isinstance(result.get("output"), str):
                raise RuntimeError(f"unexpected Modal result: {result!r}")

            remote_output = result["output"]
            remote_parts = PurePosixPath(remote_output)
            if (
                remote_parts.is_absolute()
                or len(remote_parts.parts) < 3
                or remote_parts.parts[:2] != (job_id, "output")
                or ".." in remote_parts.parts
            ):
                raise RuntimeError(f"unexpected Modal output path: {remote_output!r}")
            jobs.reload()
            print("Downloading converted video...", flush=True)
            temporary = output_path.with_name(f".{output_path.name}.{job_id}.part")
            try:
                with temporary.open("wb") as destination:
                    for chunk in jobs.read_file(remote_output):
                        destination.write(chunk)
                os.replace(temporary, output_path)
            finally:
                temporary.unlink(missing_ok=True)
        except Exception as error:
            primary_error = error
            raise RuntimeError(f"Modal conversion failed (job {job_id}): {error}") from error
        finally:
            if keep_work:
                print(f"Modal work retained in {JOBS_VOLUME}/{job_id}")
            else:
                try:
                    jobs.remove_file(job_id, recursive=True)
                except Exception as cleanup_error:
                    message = (
                        f"Could not clean Modal job {JOBS_VOLUME}/{job_id}: "
                        f"{cleanup_error}"
                    )
                    if primary_error is None:
                        raise RuntimeError(message) from cleanup_error
                    print(f"flat2vr: warning: {message}", file=sys.stderr)
