from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flat2vr.docker_backend import DockerBackend
from flat2vr.options import ConversionOptions


class DockerBackendTests(unittest.TestCase):
    def test_local_command(self) -> None:
        backend = DockerBackend(docker_host="tcp://gpu:2376")
        self.assertEqual(
            backend.command("version"),
            ["docker", "--host", "tcp://gpu:2376", "version"],
        )

    def test_remote_sudo_command(self) -> None:
        backend = DockerBackend(docker_ssh="beast", docker_sudo=True)
        self.assertEqual(
            backend.command("info"),
            ["ssh", "-o", "BatchMode=yes", "beast", "sudo -n docker info"],
        )

    def test_rejects_ambiguous_daemon(self) -> None:
        with self.assertRaisesRegex(ValueError, "either"):
            DockerBackend(docker_host="tcp://gpu:2376", docker_ssh="gpu")

    def test_conversion_forwards_diagnostics_and_retains_container(self) -> None:
        backend = DockerBackend(model_path="/models")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"input")
            with (
                patch.object(backend, "image_exists", return_value=True),
                patch.object(backend, "_copy_input"),
                patch.object(backend, "_copy_output"),
                patch("flat2vr.docker_backend.run") as run,
            ):
                backend.convert(
                    source,
                    root / "output.mp4",
                    ConversionOptions(),
                    keep_work=True,
                    verbose=True,
                )
        create_command = run.call_args_list[0].args[0]
        self.assertIn("--keep-work", create_command)
        self.assertIn("--verbose", create_command)
        self.assertFalse(
            any(call.args[0][1:3] == ["rm", "--force"] for call in run.call_args_list)
        )

    def test_conversion_removes_container_by_default(self) -> None:
        backend = DockerBackend(model_path="/models")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            source.write_bytes(b"input")
            with (
                patch.object(backend, "image_exists", return_value=True),
                patch.object(backend, "_copy_input"),
                patch.object(backend, "_copy_output"),
                patch("flat2vr.docker_backend.run") as run,
            ):
                backend.convert(source, root / "output.mp4", ConversionOptions())
        self.assertTrue(
            any(call.args[0][1:3] == ["rm", "--force"] for call in run.call_args_list)
        )


if __name__ == "__main__":
    unittest.main()
