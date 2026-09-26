"""Real Streamlit reruns against isolated files, never the user's database."""
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import toml

from supplemind.tracker import Tracker
from supplemind.ui import csv_bytes

APP = str(Path(__file__).resolve().parent.parent / "app.py")


class UITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "test.db")
        self.env = patch.dict(os.environ, {"SUPPLEMIND_DB": self.path})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_empty_app_and_add_form(self):
        app = AppTest.from_file(APP).run()
        self.assertFalse(app.exception)
        app.text_input[1].set_value("UI 測試品")
        next(button for button in app.button if button.label == "加入我的保健品").click().run()
        self.assertFalse(app.exception)
        with Tracker(self.path) as manager:
            self.assertEqual(manager.items()[0].name, "UI 測試品")

    def test_take_skip_undo_and_archive(self):
        with Tracker(self.path) as manager:
            item = manager.save_item(name="測試品", unit="粒", stock=10, warning=2,
                                     expiry=(date.today() + timedelta(days=365)).isoformat(),
                                     profile={"frequency": "每天", "slots": [{"time": "08:00", "dose": 1}, {"time": "20:00", "dose": 2}]})
        app = AppTest.from_file(APP).run()
        self.assertFalse(app.exception)
        app.button(key=f"take_{item.id}_08:00").click().run()
        self.assertFalse(app.exception)
        app.button(key=f"skip_{item.id}_20:00").click().run()
        self.assertFalse(app.exception)
        with Tracker(self.path) as manager:
            self.assertEqual(manager.get_supplement(item.id).stock_quantity, 9)
            event = next(event for event in manager.events(date.today(), date.today()) if event["status"] == "taken")
        app.button(key=f"undo_{event['id']}").click().run()
        self.assertFalse(app.exception)
        app.button(key=f"archive_{item.id}").click().run()
        self.assertFalse(app.exception)
        with Tracker(self.path) as manager:
            self.assertEqual(manager.get_supplement(item.id).stock_quantity, 10)
            self.assertEqual(manager.items(), [])

    def test_csv_is_utf8_and_spreadsheet_safe(self):
        data = csv_bytes([{"名稱": "=CMD()", "備註": "繁體中文"}]).decode("utf-8-sig")
        self.assertIn("'=CMD()", data)
        self.assertIn("繁體中文", data)

    def test_theme_config_is_valid_and_local_only(self):
        config = toml.loads((Path(APP).parent / ".streamlit" / "config.toml").read_text())
        self.assertEqual(config["theme"]["base"], "light")
        self.assertFalse(config["browser"]["gatherUsageStats"])
        self.assertEqual(config["server"]["address"], "127.0.0.1")