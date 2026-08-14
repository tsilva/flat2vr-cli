from pathlib import Path
import unittest

from flat2vr.options import ConversionOptions, default_output_path


class ConversionOptionsTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        options = ConversionOptions(disparity=0.04, encoder="libx265")
        self.assertEqual(ConversionOptions.from_dict(options.to_dict()), options)

    def test_container_arguments_cover_all_fields(self) -> None:
        arguments = ConversionOptions().container_args()
        self.assertIn("--encoder", arguments)
        self.assertIn("--depth-steps", arguments)
        self.assertEqual(arguments[arguments.index("--fps") + 1], "24")

    def test_rejects_unknown_remote_option(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown conversion options"):
            ConversionOptions.from_dict({"surprise": True})

    def test_default_output(self) -> None:
        self.assertEqual(
            default_output_path(Path("movie.mov")),
            Path("movie_Full_SBS.mp4"),
        )


if __name__ == "__main__":
    unittest.main()
