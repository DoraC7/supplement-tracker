import unittest
from datetime import date, datetime, timedelta

from supplemind import (
    InsufficientStockError,
    SupplementManager,
    SupplementNotFoundError,
    ValidationError,
)


class SupplementManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SupplementManager(":memory:")

    def tearDown(self) -> None:
        self.manager.close()

    def test_add_and_get_supplement(self) -> None:
        supplement = self.manager.add_supplement(
            name="維他命 B群",
            unit="粒",
            stock=15,
            warning_level=10,
            expiry_date="2027-01-01",
        )

        fetched = self.manager.get_supplement(supplement.id)

        self.assertEqual(fetched.name, "維他命 B群")
        self.assertEqual(fetched.stock_quantity, 15)
        self.assertEqual(fetched.unit, "粒")

    def test_duplicate_name_is_rejected(self) -> None:
        self.manager.add_supplement(
            name="魚油",
            unit="粒",
            stock=30,
            warning_level=5,
            expiry_date="2027-01-01",
        )

        with self.assertRaises(ValidationError):
            self.manager.add_supplement(
                name="魚油",
                unit="粒",
                stock=20,
                warning_level=5,
                expiry_date="2027-06-01",
            )

    def test_take_supplement_creates_log_and_reduces_stock(self) -> None:
        supplement = self.manager.add_supplement(
            name="益生菌",
            unit="包",
            stock=5,
            warning_level=2,
            expiry_date="2027-01-01",
        )
        taken_at = datetime.now().replace(microsecond=0)

        log = self.manager.take_supplement(
            supp_id=supplement.id,
            dosage=1,
            taken_at=taken_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        updated = self.manager.get_supplement(supplement.id)
        history = self.manager.get_calendar(days=30)

        self.assertEqual(log.dosage, 1)
        self.assertEqual(updated.stock_quantity, 4)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].taken_at.strftime("%Y-%m-%d %H:%M:%S"), taken_at.strftime("%Y-%m-%d %H:%M:%S"))

    def test_take_supplement_rejects_missing_id(self) -> None:
        with self.assertRaises(SupplementNotFoundError):
            self.manager.take_supplement(supp_id=999, dosage=1)

    def test_take_supplement_rejects_insufficient_stock(self) -> None:
        supplement = self.manager.add_supplement(
            name="維他命 C",
            unit="粒",
            stock=2,
            warning_level=1,
            expiry_date="2027-01-01",
        )

        with self.assertRaises(InsufficientStockError):
            self.manager.take_supplement(supp_id=supplement.id, dosage=3)

    def test_restock_supplement_increases_stock(self) -> None:
        supplement = self.manager.add_supplement(
            name="鎂",
            unit="粒",
            stock=10,
            warning_level=3,
            expiry_date="2027-01-01",
        )

        updated = self.manager.restock_supplement(supplement.id, 5)

        self.assertEqual(updated.stock_quantity, 15)

    def test_get_calendar_respects_days_filter(self) -> None:
        supplement = self.manager.add_supplement(
            name="鋅",
            unit="粒",
            stock=10,
            warning_level=3,
            expiry_date="2027-01-01",
        )
        old_dt = datetime.now().replace(microsecond=0) - timedelta(days=10)
        recent_dt = datetime.now().replace(microsecond=0) - timedelta(days=1)
        self.manager.take_supplement(
            supp_id=supplement.id,
            dosage=1,
            taken_at=old_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )
        self.manager.take_supplement(
            supp_id=supplement.id,
            dosage=1,
            taken_at=recent_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )

        logs = self.manager.get_calendar(days=3)

        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].taken_at.strftime("%Y-%m-%d"), recent_dt.strftime("%Y-%m-%d"))

    def test_check_alerts_reports_stock_and_expiry(self) -> None:
        soon = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        self.manager.add_supplement(
            name="腸胃益生菌",
            unit="包",
            stock=5,
            warning_level=7,
            expiry_date=soon,
        )

        alerts = self.manager.check_alerts(expiry_warning_days=30)
        messages = {alert.level for alert in alerts}

        self.assertIn("warning", messages)
        self.assertIn("expiry_soon", messages)


if __name__ == "__main__":
    unittest.main()
