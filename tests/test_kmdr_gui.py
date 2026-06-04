import os
import tempfile
import unittest
from unittest.mock import patch

import kmdr.gui as gui_module
from kmdr.core.console import _console_output_stream
from kmdr.core.error import ValidationError
from kmdr.core.runtime import base_url_var
from kmdr.core.structure import Credential, QuotaInfo
from kmdr.gui import (
    DownloadOptions,
    _config_with_encrypted_login,
    _default_download_dest,
    _format_volume_selection,
    _gui_config_path,
    _gui_secret_key_path,
    _has_legacy_plain_login,
    _load_gui_config,
    _save_gui_config,
    _saved_login_from_config,
)
from kmdr.gui_backend import GuiBackend, GuiBackendRunner, error_payload, success_payload
from kmdr.module.downloader.misc import DownloadTracker


class TestKmdrGuiHelpers(unittest.TestCase):
    def test_gui_no_longer_exposes_cli_command_runner(self):
        self.assertFalse(hasattr(gui_module, "KmdrCommandBuilder"))
        self.assertFalse(hasattr(gui_module, "InlineKmdrCommandRunner"))
        self.assertFalse(hasattr(gui_module, "_parse_toolcall_line"))

    def test_download_options_are_backend_options(self):
        options = DownloadOptions(
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

        self.assertEqual(options.book_url, "https://kxx.moe/c/50076.htm")
        self.assertEqual(options.volume, "1-3")
        self.assertTrue(options.use_pool)
        self.assertTrue(options.explain)

    def test_format_volume_selection(self):
        self.assertEqual(_format_volume_selection([]), "all")
        self.assertEqual(_format_volume_selection([3, 1, 2, 2, 5, 7, 8]), "1-3,5,7-8")

    def test_default_download_dest_uses_user_downloads(self):
        with patch("kmdr.gui.os.path.expanduser", return_value="C:/Users/Joe"):
            self.assertEqual(_default_download_dest(), "C:/Users/Joe/Downloads/Kmoe Manga Downloads")

    def test_success_payload_masks_dataclass_sensitive_fields(self):
        payload = success_payload({"ok": True})

        self.assertEqual(payload, {"type": "result", "code": 0, "msg": "success", "data": {"ok": True}})

    def test_error_payload_uses_kmdr_error_code(self):
        payload = error_payload(ValidationError("坏配置", field="dest"))

        self.assertEqual(payload["type"], "result")
        self.assertNotEqual(payload["code"], 0)
        self.assertIn("坏配置", payload["msg"])

    def test_download_tracker_counts_without_toolcall_mode(self):
        events = []
        tracker = DownloadTracker(2, progress_callback=lambda **payload: events.append(payload))

        tracker("completed", volume="第一卷", percentage=100)
        tracker("skipped", volume="第二卷")

        self.assertEqual(tracker.total, 2)
        self.assertEqual(tracker.completed, 1)
        self.assertEqual(tracker.skipped, 1)
        self.assertEqual(events[0]["status"], "completed")
        self.assertEqual(events[0]["volume"], "第一卷")

    def test_console_output_stream_handles_missing_std_streams(self):
        with patch("sys.stdout", None), patch("sys.__stdout__", None):
            self.assertIsNone(_console_output_stream())

    def test_backend_runner_runs_without_cli_command(self):
        with tempfile.TemporaryDirectory() as temp_home:
            with patch("kmdr.core.runtime.os.path.expanduser", return_value=temp_home):
                payload = GuiBackendRunner().run(lambda backend: backend.version())

        self.assertEqual(payload["code"], 0)
        self.assertIn("version", payload["data"])

    def test_backend_invalid_default_assignment_returns_validation_error(self):
        with tempfile.TemporaryDirectory() as temp_home:
            with patch("kmdr.core.runtime.os.path.expanduser", return_value=temp_home):
                payload = GuiBackendRunner().run(lambda backend: backend.set_download_defaults(["bad-format"]))

        self.assertEqual(payload["code"], 41)
        self.assertIn("assignment", payload["msg"])

    def test_backend_config_uses_persisted_base_url(self):
        with tempfile.TemporaryDirectory() as temp_home:
            with patch("kmdr.core.runtime.os.path.expanduser", return_value=temp_home):
                runner = GuiBackendRunner()
                set_payload = runner.run(lambda backend: backend.set_base_url("https://mox.moe"))
                list_payload = runner.run(lambda backend: backend.list_config())

        self.assertEqual(set_payload["code"], 0)
        self.assertEqual(list_payload["data"]["base_url"], "https://mox.moe")

    def test_backend_status_payload_includes_base_url_and_quota(self):
        cred = Credential(
            username="alice",
            cookies={"VLIBSID": "secret-cookie"},
            user_quota=QuotaInfo(reset_day=1, total=100.0, used=40.0),
            level=3,
            nickname="Alice",
        )
        base_url_var.set("https://mox.moe")

        data = GuiBackend._credential_status_data(cred)

        self.assertEqual(data["base_url"], "https://mox.moe")
        self.assertEqual(data["quota_remaining"], 60.0)
        self.assertEqual(data["cookies"], "***SENSITIVE***")


class TestKmdrGuiConfig(unittest.TestCase):
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
