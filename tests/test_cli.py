from pathlib import Path
import unittest

from flat2vr.cli import build_parser


class CliTests(unittest.TestCase):
    def test_minimal_convert(self) -> None:
        args = build_parser().parse_args(["convert", "sample.mp4"])
        self.assertEqual(args.backend, "docker")
        self.assertEqual(args.input, Path("sample.mp4"))
        self.assertEqual(args.encoder, "auto")
        self.assertFalse(hasattr(args, "modal_gpu"))

    def test_remote_build(self) -> None:
        args = build_parser().parse_args(
            ["build", "--docker-ssh", "beast", "--docker-sudo"]
        )
        self.assertEqual(args.docker_ssh, "beast")
        self.assertTrue(args.docker_sudo)

    def test_modal_deploy(self) -> None:
        args = build_parser().parse_args(["modal", "deploy", "--modal-gpu", "A100-80GB"])
        self.assertEqual(args.modal_gpu, "A100-80GB")


if __name__ == "__main__":
    unittest.main()
