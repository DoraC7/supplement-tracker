import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from supplemind.errors import InsufficientStockError, ValidationError
from supplemind.repository import SupplementManager
from supplemind.tracker import Tracker, validate_profile


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.manager = Tracker(":memory:")

    def tearDown(self):
        self.manager.close()

    def add(self, **overrides):
        values = dict(name="測試保健品", unit="粒", stock=10, warning=2,
                      expiry=(date.today() + timedelta(days=365)).isoformat(),
                      profile={"frequency": "每天", "slots": [{"time": "08:00", "dose": 1}, {"time": "20:00", "dose": 2}]})
        values.update(overrides)
        return self.manager.save_item(**values)

    def test_multiple_doses_are_independent_and_duplicate_is_rejected(self):
        item = self.add()
        self.manager.record(item.id, "08:00", "taken", 1)
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "08:00", "taken", 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 9)
        self.assertIsNone(self.manager.schedule()[1]["event"])
        self.manager.record(item.id, "20:00", "taken", 2)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 7)

    def test_skip_undo_and_relog_preserve_audit_and_stock(self):
        item = self.add()
        skipped = self.manager.record(item.id, "08:00", "skipped", 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.manager.undo(skipped)
        taken = self.manager.record(item.id, "08:00", "taken", 1)
        self.manager.undo(taken)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.assertEqual(self.manager.get_calendar(), [])
        self.assertEqual(len(self.manager.history(date.today(), date.today())), 2)
        with self.assertRaises(ValidationError):
            self.manager.undo(taken)

    def test_failed_intake_rolls_back(self):
        item = self.add(stock=0)
        with self.assertRaises(InsufficientStockError):
            self.manager.record(item.id, "08:00", "taken", 1)
        self.assertEqual(self.manager.events(date.today(), date.today()), [])
        self.assertEqual(self.manager.get_calendar(), [])

    def test_expired_can_be_skipped_not_taken(self):
        item = self.add(expiry=(date.today() - timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "08:00", "taken", 1)
        self.manager.record(item.id, "08:00", "skipped", 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)

    def test_weekday_and_date_boundaries(self):
        today = date.today()
        self.add(profile={"frequency": "指定星期", "weekdays": [today.weekday()],
                          "start": today.isoformat(), "end": today.isoformat(),
                          "slots": [{"time": "08:00", "dose": 1}]})
        self.assertEqual(len(self.manager.schedule(today)), 1)
        self.assertEqual(self.manager.schedule(today + timedelta(days=1)), [])
        self.assertEqual(self.manager.schedule(today - timedelta(days=1)), [])

    def test_as_needed_is_not_scheduled(self):
        item = self.add(profile={"frequency": "需要時"})
        self.assertEqual(self.manager.schedule(), [])
        self.manager.record(item.id, "as_needed", "taken", 1)
        self.manager.record(item.id, "as_needed", "taken", 2)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 7)

    def test_as_needed_respects_start_date(self):
        item = self.add(profile={"frequency": "需要時", "start": (date.today() + timedelta(days=1)).isoformat()})
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "as_needed", "taken", 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)

    def test_archive_restores_schedule_and_retains_history(self):
        item = self.add()
        self.manager.record(item.id, "08:00", "taken", 1)
        self.manager.archive(item.id)
        self.assertEqual(self.manager.schedule(), [])
        self.assertEqual(len(self.manager.history(date.today(), date.today())), 1)
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "20:00", "taken", 2)
        self.manager.archive(item.id, False)
        self.assertEqual(len(self.manager.schedule()), 2)

    def test_edit_preserves_stock_and_event_snapshot(self):
        item = self.add()
        self.manager.record(item.id, "08:00", "taken", 1)
        self.add(supplement_id=item.id, name="新名稱", stock=100)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 9)
        self.assertEqual(self.manager.history(date.today(), date.today())[0]["name"], "測試保健品")

    def test_invalid_schedule_does_not_create_partial_item(self):
        with self.assertRaises(ValidationError):
            self.add(profile={"frequency": "每天", "slots": [{"time": "29:99", "dose": 1}]})
        self.assertEqual(self.manager.list_supplements(), [])

    def test_nonfinite_and_duplicate_times_rejected(self):
        for value in [float("nan"), float("inf"), -1, 0]:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                validate_profile({"default_dose": value})
        with self.assertRaises(ValidationError):
            validate_profile({"frequency": "每天", "slots": [{"time": "08:00", "dose": 1}] * 2})

    def test_legacy_database_is_preserved_and_migration_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "old.db")
            with SupplementManager(path) as old:
                item = old.add_supplement("既有資料", "粒", 10, 2, "2099-01-01")
                old.take_supplement(item.id, 1)
            for _ in range(2):
                with Tracker(path) as tracker:
                    self.assertEqual(tracker.get_supplement(item.id).stock_quantity, 9)
                    self.assertEqual(tracker.profile(item.id)["frequency"], "尚未設定")
                    self.assertEqual(tracker.schedule(), [])
                    self.assertEqual(tracker.history(date.today(), date.today())[0]["slot"], "legacy")


if __name__ == "__main__":
    unittest.main()