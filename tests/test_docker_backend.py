import unittest

from flat2vr.docker_backend import DockerBackend


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


if __name__ == "__main__":
    unittest.main()
