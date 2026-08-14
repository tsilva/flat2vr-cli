from contextlib import nullcontext
from contextlib import redirect_stderr
import io
from pathlib import Path
from types import SimpleNamespace
import subprocess
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flat2vr.modal_backend import ModalBackend, ensure_modal_authentication
from flat2vr.modal_contract import deployment_tags
from flat2vr.options import ConversionOptions


class FakeBatch:
    def __init__(self) -> None:
        self.upload: tuple[str, str] | None = None

    def __enter__(self):
        return self

    def __exit__(self, *unused) -> None:
        pass

    def put_file(self, local: str, remote: str) -> None:
        self.upload = (local, remote)


class FakeJobs:
    def __init__(self) -> None:
        self.batch = FakeBatch()
        self.reloaded = False
        self.removed: tuple[str, bool] | None = None

    def batch_upload(self, *, force: bool):
        assert force
        return self.batch

    def reload(self) -> None:
        self.reloaded = True

    def read_file(self, path: str):
        self.read_path = path
        yield b"converted-video"

    def remove_file(self, path: str, *, recursive: bool) -> None:
        self.removed = (path, recursive)


class FakeFunction:
    def remote(self, job_id: str, request: dict[str, object]):
        self.job_id = job_id
        self.request = request
        return {"output": f"{job_id}/output/result_Full_SBS.mp4"}


class ModalBackendTests(unittest.TestCase):
    def test_successful_job_uses_protocol_downloads_and_cleans_up(self) -> None:
        jobs = FakeJobs()
        function = FakeFunction()
        fake_modal = SimpleNamespace(
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: jobs),
            Function=SimpleNamespace(from_name=lambda *args, **kwargs: function),
            enable_output=nullcontext,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "output.mp4"
            source.write_bytes(b"input-video")
            with patch("flat2vr.modal_backend._modal", return_value=fake_modal):
                ModalBackend().convert(source, output, ConversionOptions())

            self.assertEqual(output.read_bytes(), b"converted-video")
            self.assertTrue(jobs.reloaded)
            self.assertEqual(jobs.removed, (function.job_id, True))
            self.assertEqual(function.request["protocol"], 1)
            self.assertTrue(jobs.batch.upload[1].endswith("/input/source.mp4"))

    def test_keep_work_skips_remote_cleanup(self) -> None:
        jobs = FakeJobs()
        function = FakeFunction()
        fake_modal = SimpleNamespace(
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: jobs),
            Function=SimpleNamespace(from_name=lambda *args, **kwargs: function),
            enable_output=nullcontext,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"input")
            with patch("flat2vr.modal_backend._modal", return_value=fake_modal):
                ModalBackend().convert(
                    source,
                    root / "output.mp4",
                    ConversionOptions(),
                    keep_work=True,
                )
        self.assertIsNone(jobs.removed)
        self.assertTrue(function.request["keep_work"])

    def test_upload_failure_still_cleans_remote_job(self) -> None:
        jobs = FakeJobs()
        jobs.batch.put_file = MagicMock(side_effect=OSError("upload failed"))
        fake_modal = SimpleNamespace(
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: jobs),
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            with patch("flat2vr.modal_backend._modal", return_value=fake_modal):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    ModalBackend().convert(
                        source,
                        Path(directory) / "output.mp4",
                        ConversionOptions(),
                    )
        self.assertIsNotNone(jobs.removed)

    def test_cleanup_failure_does_not_hide_conversion_failure(self) -> None:
        jobs = FakeJobs()
        jobs.batch.put_file = MagicMock(side_effect=OSError("upload failed"))
        jobs.remove_file = MagicMock(side_effect=OSError("cleanup failed"))
        fake_modal = SimpleNamespace(
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: jobs),
        )
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            with (
                patch("flat2vr.modal_backend._modal", return_value=fake_modal),
                redirect_stderr(errors),
            ):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    ModalBackend().convert(
                        source,
                        Path(directory) / "output.mp4",
                        ConversionOptions(),
                    )
        self.assertIn("cleanup failed", errors.getvalue())

    def test_rejects_output_outside_the_job(self) -> None:
        jobs = FakeJobs()
        function = FakeFunction()
        function.remote = MagicMock(return_value={"output": "another-job/output.mp4"})
        fake_modal = SimpleNamespace(
            Volume=SimpleNamespace(from_name=lambda *args, **kwargs: jobs),
            Function=SimpleNamespace(from_name=lambda *args, **kwargs: function),
            enable_output=nullcontext,
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            with patch("flat2vr.modal_backend._modal", return_value=fake_modal):
                with self.assertRaisesRegex(RuntimeError, "output path"):
                    ModalBackend().convert(
                        source,
                        Path(directory) / "output.mp4",
                        ConversionOptions(),
                    )
        self.assertIsNotNone(jobs.removed)

    def test_setup_deploys_only_when_tags_are_stale(self) -> None:
        backend = ModalBackend()
        with (
            patch("flat2vr.modal_backend.ensure_modal_authentication"),
            patch.object(backend, "_deployed_tags", side_effect=[{}, deployment_tags()]),
            patch.object(backend, "_function_exists", return_value=True),
            patch.object(backend, "_deploy") as deploy,
        ):
            backend.setup(interactive=False)
        deploy.assert_called_once_with(verbose=False)

    def test_setup_skips_current_deployment(self) -> None:
        backend = ModalBackend()
        with (
            patch("flat2vr.modal_backend.ensure_modal_authentication"),
            patch.object(backend, "_deployed_tags", return_value=deployment_tags()),
            patch.object(backend, "_function_exists", return_value=True),
            patch.object(backend, "_deploy") as deploy,
        ):
            backend.setup(interactive=False)
        deploy.assert_not_called()

    def test_setup_redeploys_when_function_is_missing(self) -> None:
        backend = ModalBackend()
        with (
            patch("flat2vr.modal_backend.ensure_modal_authentication"),
            patch.object(backend, "_deployed_tags", return_value=deployment_tags()),
            patch.object(backend, "_function_exists", side_effect=[False, True]),
            patch.object(backend, "_deploy") as deploy,
        ):
            backend.setup(interactive=False)
        deploy.assert_called_once_with(verbose=False)

    def test_noninteractive_authentication_failure_is_actionable(self) -> None:
        failed = subprocess.CompletedProcess(["python"], 1, "", "missing token")
        with patch("flat2vr.modal_backend._authentication_probe", return_value=failed):
            with self.assertRaisesRegex(RuntimeError, "flat2vr setup modal"):
                ensure_modal_authentication(interactive=False)

    def test_interactive_authentication_runs_modal_setup_and_retries(self) -> None:
        failed = subprocess.CompletedProcess(["python"], 1, "", "missing token")
        passed = subprocess.CompletedProcess(["python"], 0, "", "")
        with (
            patch(
                "flat2vr.modal_backend._authentication_probe",
                side_effect=[failed, passed],
            ),
            patch("flat2vr.modal_backend.subprocess.run") as run,
        ):
            ensure_modal_authentication(interactive=True)
        run.assert_called_once()
        self.assertIn("modal", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
