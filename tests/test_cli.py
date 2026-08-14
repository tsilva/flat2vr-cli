from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from flat2vr.cli import (
    _conversion_options,
    build_parser,
    build_setup_parser,
    dispatch_conversion,
    dispatch_setup,
)
from flat2vr.configuration import Configuration, DockerConfiguration


class CliTests(unittest.TestCase):
    def test_minimal_conversion_is_the_root_action(self) -> None:
        args = build_parser().parse_args(["sample.mp4"])
        self.assertEqual(args.input, Path("sample.mp4"))
        self.assertEqual(args.preset, "balanced")
        self.assertEqual(args.strength, "normal")

    def test_normal_help_hides_expert_flags(self) -> None:
        normal = build_parser().format_help()
        advanced = build_parser(advanced=True).format_help()
        self.assertNotIn("--depth-steps", normal)
        self.assertNotIn("--keep-work", normal)
        self.assertIn("--depth-steps", advanced)
        self.assertIn("--keep-work", advanced)

    def test_no_arguments_prints_help_without_backend_work(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertIsNone(dispatch_conversion(build_parser().parse_args([])))
        self.assertIn("flat2vr movie.mp4", output.getvalue())

    def test_advanced_values_override_profile(self) -> None:
        args = build_parser().parse_args(
            ["sample.mp4", "--preset", "fast", "--strength", "strong", "--depth-steps", "4"]
        )
        options = _conversion_options(args)
        self.assertEqual(options.depth_steps, 4)
        self.assertEqual(options.output_height, 720)
        self.assertEqual(options.disparity, 0.07)

    def test_existing_output_fails_before_loading_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mp4"
            output = root / "result.mp4"
            source.write_bytes(b"input")
            output.write_bytes(b"existing")
            args = build_parser().parse_args([str(source), "-o", str(output)])
            with patch("flat2vr.cli.load_configuration") as load:
                with self.assertRaisesRegex(FileExistsError, "--overwrite"):
                    dispatch_conversion(args)
            load.assert_not_called()

    def test_output_cannot_replace_input_or_use_the_wrong_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            same = build_parser().parse_args(
                [str(source), "-o", str(source), "--overwrite"]
            )
            with self.assertRaisesRegex(ValueError, "different from the input"):
                dispatch_conversion(same)
            wrong = build_parser().parse_args(
                [str(source), "-o", str(source.with_suffix(".mov"))]
            )
            with self.assertRaisesRegex(ValueError, "end in .mp4"):
                dispatch_conversion(wrong)

    def test_first_modal_conversion_sets_up_saves_and_converts(self) -> None:
        backend = MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(b"input")
            args = build_parser().parse_args([str(source)])
            with (
                patch("flat2vr.cli.load_configuration", return_value=None),
                patch("flat2vr.cli.save_configuration") as save,
                patch("flat2vr.cli._confirm_first_modal_setup"),
                patch("flat2vr.cli.ModalBackend", return_value=backend),
            ):
                dispatch_conversion(args)
        backend.setup.assert_called_once_with(interactive=True, verbose=False)
        backend.convert.assert_called_once()
        save.assert_called_once_with(Configuration())

    def test_setup_docker_remembers_target(self) -> None:
        backend = MagicMock()
        args = build_setup_parser().parse_args(
            ["docker", "ssh://gpu.example.com", "--sudo"]
        )
        with (
            patch("flat2vr.cli.load_configuration", return_value=None),
            patch("flat2vr.cli.save_configuration", return_value=Path("config.json")) as save,
            patch("flat2vr.cli._docker_backend", return_value=backend),
        ):
            result = dispatch_setup(args)
        self.assertEqual(result.backend, "docker")
        self.assertEqual(
            result.docker,
            DockerConfiguration(target="ssh://gpu.example.com", sudo=True),
        )
        backend.setup.assert_called_once_with(rebuild=False, verbose=False)
        save.assert_called_once_with(result)

    def test_setup_without_backend_repairs_current_selection(self) -> None:
        current = Configuration(backend="docker")
        backend = MagicMock()
        args = build_setup_parser().parse_args([])
        with (
            patch("flat2vr.cli.load_configuration", return_value=current),
            patch("flat2vr.cli.save_configuration", return_value=Path("config.json")),
            patch("flat2vr.cli._docker_backend", return_value=backend),
        ):
            result = dispatch_setup(args)
        self.assertEqual(result.backend, "docker")
        backend.setup.assert_called_once()

    def test_switching_back_to_docker_reuses_its_saved_target(self) -> None:
        docker = DockerConfiguration(target="ssh://gpu.example.com", sudo=True)
        current = Configuration(backend="modal", docker=docker)
        backend = MagicMock()
        args = build_setup_parser().parse_args(["docker"])
        with (
            patch("flat2vr.cli.load_configuration", return_value=current),
            patch("flat2vr.cli.save_configuration", return_value=Path("config.json")),
            patch("flat2vr.cli._docker_backend", return_value=backend),
        ):
            result = dispatch_setup(args)
        self.assertEqual(result.docker, docker)


if __name__ == "__main__":
    unittest.main()
