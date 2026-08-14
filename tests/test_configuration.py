import json
import os
from pathlib import Path
import tempfile
import unittest

from flat2vr.configuration import (
    Configuration,
    ConfigurationError,
    DockerConfiguration,
    load_configuration,
    save_configuration,
)


class ConfigurationTests(unittest.TestCase):
    def test_atomic_round_trip_contains_no_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            expected = Configuration(
                backend="docker",
                docker=DockerConfiguration(
                    target="ssh://gpu.example.com",
                    sudo=True,
                    model_path="/models",
                ),
            )
            self.assertEqual(save_configuration(expected, path), path)
            self.assertEqual(load_configuration(path), expected)
            payload = path.read_text(encoding="utf-8")
            self.assertNotIn("token", payload.lower())
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_configuration_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsNone(load_configuration(Path(directory) / "missing.json"))

    def test_invalid_configuration_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"version": 99}), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "flat2vr setup"):
                load_configuration(path)

    def test_rejects_sudo_without_ssh(self) -> None:
        with self.assertRaisesRegex(ConfigurationError, "only for ssh"):
            DockerConfiguration(target="tcp://gpu:2376", sudo=True).validate()


if __name__ == "__main__":
    unittest.main()
