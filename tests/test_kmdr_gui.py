import unittest
from unittest.mock import patch

from kmdr.gui import (
    DownloadOptions,
    KmdrCommandBuilder,
    _bundled_cli_executable,
    _mask_sensitive_args,
    _parse_toolcall_line,
    _subprocess_creation_flags,
)


class TestKmdrGuiCommandBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = KmdrCommandBuilder(python_executable="python", module="kmdr.main")

    def test_login_command_masks_password(self):
        command = self.builder.login("user@example.com", "secret")

        self.assertEqual(
            command,
            ["python", "-m", "kmdr.main", "--mode", "toolcall", "login", "-u", "user@example.com", "-p", "secret"],
        )
        self.assertEqual(
            _mask_sensitive_args(command),
            ["python", "-m", "kmdr.main", "--mode", "toolcall", "login", "-u", "user@example.com", "-p", "******"],
        )

    def test_download_command_skips_empty_options(self):
        command = self.builder.download(
            DownloadOptions(
                book_url="https://kxx.moe/c/50076.htm",
                dest="/tmp/manga",
                volume="1-3",
                vol_type="extra",
                book_format="epub",
                method="1",
                retry="3",
                num_workers="8",
                use_pool=True,
                explain=True,
            )
        )

        self.assertEqual(
            command,
            [
                "python",
                "-m",
                "kmdr.main",
                "--mode",
                "toolcall",
                "download",
                "-d",
                "/tmp/manga",
                "-l",
                "https://kxx.moe/c/50076.htm",
                "-v",
                "1-3",
                "-t",
                "extra",
                "-f",
                "epub",
                "-m",
                "1",
                "-r",
                "3",
                "--num-workers",
                "8",
                "--use-pool",
                "--explain",
            ],
        )

    def test_config_commands(self):
        self.assertEqual(
            self.builder.config_set_base_url("https://mox.moe"),
            ["python", "-m", "kmdr.main", "--mode", "toolcall", "config", "--base-url", "https://mox.moe"],
        )
        self.assertEqual(
            self.builder.config_set(["dest=/tmp/manga", "retry=3"]),
            ["python", "-m", "kmdr.main", "--mode", "toolcall", "config", "--set", "dest=/tmp/manga", "retry=3"],
        )
        self.assertEqual(
            self.builder.config_list(),
            ["python", "-m", "kmdr.main", "--mode", "toolcall", "config", "--list-option"],
        )

    def test_parse_toolcall_line(self):
        self.assertEqual(
            _parse_toolcall_line('{"type":"result","code":0,"data":{"ok":true}}'),
            {"type": "result", "code": 0, "data": {"ok": True}},
        )
        self.assertIsNone(_parse_toolcall_line("plain log"))

    def test_bundled_cli_command(self):
        builder = KmdrCommandBuilder(python_executable=r"C:\app\kmdr-cli.exe")

        self.assertEqual(
            builder.version(),
            [r"C:\app\kmdr-cli.exe", "--mode", "toolcall", "version"],
        )

    @patch("kmdr.gui.os.path.exists", return_value=True)
    @patch("kmdr.gui.os.name", "nt")
    @patch("kmdr.gui.sys.executable", r"C:\app\Kmoe Manga Downloader.exe")
    @patch("kmdr.gui.sys.frozen", True, create=True)
    def test_bundled_cli_executable(self, _exists):
        self.assertEqual(
            _bundled_cli_executable(),
            r"C:\app\kmdr-cli.exe",
        )

    @patch("kmdr.gui.os.name", "posix")
    def test_subprocess_creation_flags_posix(self):
        self.assertEqual(_subprocess_creation_flags(), 0)


if __name__ == "__main__":
    unittest.main()
