from pathlib import Path
import unittest

from flat2vr.options import ConversionOptions, default_output_path


class ConversionOptionsTests(unittest.TestCase):
    def test_presets_have_exact_contract(self) -> None:
        fast = ConversionOptions.from_profile(preset="fast")
        balanced = ConversionOptions.from_profile(preset="balanced")
        best = ConversionOptions.from_profile(preset="best")
        self.assertEqual((fast.depth_steps, fast.output_height, fast.quality), (1, 720, 23))
        self.assertEqual(
            (balanced.depth_steps, balanced.output_height, balanced.quality),
            (5, 1024, 19),
        )
        self.assertEqual((best.depth_steps, best.output_height, best.quality), (5, 1024, 17))

    def test_strength_and_advanced_override_precedence(self) -> None:
        options = ConversionOptions.from_profile(
            preset="fast",
            strength="strong",
            overrides={"depth_steps": 4, "quality": None},
        )
        self.assertEqual(options.disparity, 0.07)
        self.assertEqual(options.depth_steps, 4)
        self.assertEqual(options.quality, 23)

    def test_request_round_trip(self) -> None:
        options = ConversionOptions(disparity=0.04, encoder="libx265")
        decoded, verbose, keep_work = ConversionOptions.from_request(
            options.to_request(verbose=True, keep_work=True)
        )
        self.assertEqual(decoded, options)
        self.assertTrue(verbose)
        self.assertTrue(keep_work)

    def test_rejects_wrong_protocol_and_unknown_fields(self) -> None:
        request = ConversionOptions().to_request()
        request["protocol"] = 99
        with self.assertRaisesRegex(ValueError, "unsupported conversion protocol"):
            ConversionOptions.from_request(request)
        with self.assertRaisesRegex(ValueError, "unknown conversion options"):
            ConversionOptions.from_dict({"surprise": True})

    def test_container_arguments_cover_all_fields(self) -> None:
        arguments = ConversionOptions().container_args()
        self.assertIn("--encoder", arguments)
        self.assertIn("--depth-steps", arguments)
        self.assertEqual(arguments[arguments.index("--fps") + 1], "24")

    def test_default_output(self) -> None:
        self.assertEqual(
            default_output_path(Path("movie.mov")),
            Path("movie_Full_SBS.mp4"),
        )


if __name__ == "__main__":
    unittest.main()
