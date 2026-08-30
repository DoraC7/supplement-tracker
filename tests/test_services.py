import unittest
from datetime import date, datetime, timedelta

from supplemind import SupplementManager
from supplemind.models import IntakeLog, Supplement
from supplemind.services import (
    estimate_consumption_rate,
    forecast_all,
    get_daily_briefing,
)


class EstimateConsumptionRateTests(unittest.TestCase):
    """Pure unit tests: no database involved, just dataclasses."""

    def _supplement(self, stock: float = 30) -> Supplement:
        return Supplement(
            id=1,
            name="魚油",
            unit="粒",
            stock_quantity=stock,
            warning_level=5,
            expiry_date=date(2027, 1, 1),
        )

    def _log(self, days_ago: int, dosage: float = 1) -> IntakeLog:
        return IntakeLog(
            id=1,
            supplement_id=1,
            name="魚油",
            taken_at=datetime.now() - timedelta(days=days_ago),
            dosage=dosage,
            unit="粒",
        )

    def test_no_logs_returns_no_estimate(self) -> None:
        forecast = estimate_consumption_rate(self._supplement(), [])

        self.assertEqual(forecast.daily_rate, 0.0)
        self.assertIsNone(forecast.days_remaining)

    def test_rate_uses_actual_history_span_not_lookback_window(self) -> None:
        # Only 3 days of history, one dose per day -> rate should be ~1/day,
        # not diluted by an arbitrary 30-day lookback window.
        logs = [self._log(days_ago=0), self._log(days_ago=1), self._log(days_ago=2)]

        forecast = estimate_consumption_rate(self._supplement(stock=30), logs, today=date.today())

        self.assertAlmostEqual(forecast.daily_rate, 1.0, places=2)
        self.assertAlmostEqual(forecast.days_remaining, 30.0, places=1)

    def test_higher_dosage_reduces_days_remaining(self) -> None:
        logs = [self._log(days_ago=0, dosage=3), self._log(days_ago=1, dosage=3)]

        forecast = estimate_consumption_rate(self._supplement(stock=30), logs, today=date.today())

        self.assertAlmostEqual(forecast.daily_rate, 3.0, places=2)
        self.assertAlmostEqual(forecast.days_remaining, 10.0, places=1)


class ForecastAllTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SupplementManager(":memory:")

    def tearDown(self) -> None:
        self.manager.close()

    def test_forecast_all_sorts_soonest_depletion_first_and_unknown_last(self) -> None:
        low = self.manager.add_supplement(
            name="益生菌", unit="包", stock=5, warning_level=2, expiry_date="2027-01-01"
        )
        high = self.manager.add_supplement(
            name="維他命D", unit="粒", stock=100, warning_level=10, expiry_date="2027-01-01"
        )
        never_taken = self.manager.add_supplement(
            name="鎂", unit="粒", stock=60, warning_level=10, expiry_date="2027-01-01"
        )

        self.manager.take_supplement(low.id, dosage=1)
        self.manager.take_supplement(high.id, dosage=1)

        forecasts = forecast_all(self.manager, lookback_days=30)

        self.assertEqual(len(forecasts), 3)
        # The item with the least remaining days should come first.
        self.assertEqual(forecasts[0].supplement_id, low.id)
        self.assertEqual(forecasts[1].supplement_id, high.id)
        # The never-taken item has no usable history and sorts last.
        self.assertEqual(forecasts[2].supplement_id, never_taken.id)
        self.assertIsNone(forecasts[2].days_remaining)


class DailyBriefingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = SupplementManager(":memory:")

    def tearDown(self) -> None:
        self.manager.close()

    def test_briefing_splits_taken_and_pending_today(self) -> None:
        taken = self.manager.add_supplement(
            name="魚油", unit="粒", stock=30, warning_level=5, expiry_date="2027-01-01"
        )
        pending = self.manager.add_supplement(
            name="鎂", unit="粒", stock=30, warning_level=5, expiry_date="2027-01-01"
        )

        self.manager.take_supplement(taken.id, dosage=1)

        briefing = get_daily_briefing(self.manager)

        self.assertEqual(len(briefing.taken_today), 1)
        self.assertEqual(briefing.taken_today[0].supplement_id, taken.id)

        pending_ids = {supplement.id for supplement in briefing.pending_today}
        self.assertIn(pending.id, pending_ids)
        self.assertNotIn(taken.id, pending_ids)

    def test_briefing_includes_alerts_and_forecasts(self) -> None:
        self.manager.add_supplement(
            name="益生菌", unit="包", stock=1, warning_level=5, expiry_date="2027-01-01"
        )

        briefing = get_daily_briefing(self.manager)

        self.assertEqual(len(briefing.alerts), 1)
        self.assertEqual(briefing.alerts[0].level, "warning")
        self.assertEqual(len(briefing.forecasts), 1)


if __name__ == "__main__":
    unittest.main()
