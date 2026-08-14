from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from flat2vr.modal_backend import ModalBackend
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
    def remote(self, job_id: str, options: dict[str, object]):
        self.job_id = job_id
        self.options = options
        return {"output": f"{job_id}/output/result_Full_SBS.mp4"}


class ModalBackendTests(unittest.TestCase):
    def test_successful_job_uploads_runs_downloads_and_cleans_up(self) -> None:
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
            self.assertTrue(jobs.batch.upload[1].endswith("/input/source.mp4"))


if __name__ == "__main__":
    unittest.main()
