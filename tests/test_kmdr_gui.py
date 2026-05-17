import os
import tempfile
import unittest
from unittest.mock import patch

from kmdr.gui import (
    DownloadOptions,
    KmdrCommandBuilder,
    _bundled_cli_executable,
    _config_with_encrypted_login,
    _format_volume_selection,
    _gui_config_path,
    _gui_secret_key_path,
    _has_legacy_plain_login,
    _load_gui_config,
    _mask_sensitive_args,
    _parse_toolcall_line,
    _save_gui_config,
    _saved_login_from_config,
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

    def test_format_volume_selection(self):
        self.assertEqual(_format_volume_selection([]), "all")
        self.assertEqual(_format_volume_selection([3, 1, 2, 2, 5, 7, 8]), "1-3,5,7-8")

    def test_bundled_cli_command(self):
        builder = KmdrCommandBuilder(python_executable=r"C:\app\kmdr-cli.exe")

        self.assertEqual(
            builder.version(),
            [r"C:\app\kmdr-cli.exe", "--mode", "toolcall", "version"],
        )

    @patch("kmdr.gui.os.path.exists", return_value=False)
    @patch("kmdr.gui.os.name", "nt")
    @patch("kmdr.gui.sys._MEIPASS", r"C:\Temp\_MEI123", create=True)
    @patch("kmdr.gui.sys.executable", r"C:\app\Kmoe Manga Downloader.exe")
    @patch("kmdr.gui.sys.frozen", True, create=True)
    def test_bundled_cli_executable(self, _exists):
        self.assertEqual(
            _bundled_cli_executable(),
            r"C:\app\Kmoe Manga Downloader.exe",
        )

    @patch("kmdr.gui.os.path.exists", return_value=True)
    @patch("kmdr.gui.os.name", "nt")
    @patch("kmdr.gui.sys._MEIPASS", None, create=True)
    @patch("kmdr.gui.sys.executable", r"C:\app\Kmoe Manga Downloader.exe")
    @patch("kmdr.gui.sys.frozen", True, create=True)
    def test_bundled_cli_executable_prefers_sidecar_for_onedir(self, _exists):
        self.assertEqual(
            _bundled_cli_executable(),
            r"C:\app\kmdr-cli.exe",
        )

    @patch("kmdr.gui.sys.frozen", True, create=True)
    def test_frozen_gui_command_runs_internal_cli(self):
        builder = KmdrCommandBuilder(python_executable=r"C:\app\Kmoe Manga Downloader.exe")

        self.assertEqual(
            builder.version(),
            [r"C:\app\Kmoe Manga Downloader.exe", "--kmdr-cli", "--mode", "toolcall", "version"],
        )

    @patch("kmdr.gui.os.name", "posix")
    def test_subprocess_creation_flags_posix(self):
        self.assertEqual(_subprocess_creation_flags(), 0)

    def test_gui_config_round_trip(self):
        with tempfile.TemporaryDirectory() as temp_home:
            with patch("kmdr.gui.os.path.expanduser", return_value=temp_home):
                self.assertEqual(_gui_config_path(), os.path.join(temp_home, ".kmdr-gui"))
                self.assertEqual(_load_gui_config(), {})

                config = _config_with_encrypted_login({}, "alice", "secret")
                _save_gui_config(config)

                loaded_config = _load_gui_config()
                self.assertTrue(loaded_config["remember_login"])
                self.assertIn("login_secret", loaded_config)
                self.assertNotIn("login_username", loaded_config)
                self.assertNotIn("login_password", loaded_config)
                secret_payload = loaded_config["login_secret"]["payload"]
                self.assertNotIn("alice", secret_payload)
                self.assertNotIn("secret", secret_payload)
                self.assertEqual(_saved_login_from_config(loaded_config), ("alice", "secret"))
                self.assertTrue(os.path.exists(_gui_secret_key_path()))

    def test_gui_config_ignores_invalid_json(self):
        with tempfile.TemporaryDirectory() as temp_home:
            with patch("kmdr.gui.os.path.expanduser", return_value=temp_home):
                with open(_gui_config_path(), "w", encoding="utf-8") as file:
                    file.write("{bad json")

                self.assertEqual(_load_gui_config(), {})

    def test_legacy_plain_login_config_can_be_read(self):
        config = {"remember_login": True, "login_username": "alice", "login_password": "secret"}

        self.assertTrue(_has_legacy_plain_login(config))
        self.assertEqual(_saved_login_from_config(config), ("alice", "secret"))


if __name__ == "__main__":
    unittest.main()
