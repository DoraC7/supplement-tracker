"""Business logic layer.

Functions here compose multiple repository reads and perform calculations
that do not touch the database directly. They accept an already-open
``SupplementManager`` so the pure calculation parts (e.g.
``estimate_consumption_rate``) can also be unit tested with plain
dataclasses, without needing a database connection at all.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .models import Alert, IntakeLog, Supplement
from .repository import SupplementManager

DEFAULT_LOOKBACK_DAYS = 30


@dataclass(frozen=True)
class ConsumptionForecast:
    supplement_id: int
    name: str
    unit: str
    daily_rate: float
    days_remaining: Optional[float]  # None when there is not enough history


@dataclass(frozen=True)
class DailyBriefing:
    alerts: list[Alert]
    forecasts: list[ConsumptionForecast]
    taken_today: list[IntakeLog]
    pending_today: list[Supplement]


def estimate_consumption_rate(
    supplement: Supplement,
    logs: list[IntakeLog],
    today: Optional[date] = None,
) -> ConsumptionForecast:
    """Estimate a supplement's daily consumption rate and remaining stock days.

    ``logs`` should be that supplement's own intake history within whatever
    lookback window the caller cares about (order does not matter). The
    denominator used for the rate is the actual span between the earliest
    log in the window and ``today``, rather than the fixed lookback window
    size, so a supplement with only a few days of history doesn't get its
    rate artificially diluted.
    """
    reference_day = today or date.today()

    if not logs:
        return ConsumptionForecast(
            supplement_id=supplement.id,
            name=supplement.name,
            unit=supplement.unit,
            daily_rate=0.0,
            days_remaining=None,
        )

    total_dosage = sum(log.dosage for log in logs)
    earliest_day = min(log.taken_at.date() for log in logs)
    span_days = max(1, (reference_day - earliest_day).days + 1)
    daily_rate = total_dosage / span_days

    days_remaining = (supplement.stock_quantity / daily_rate) if daily_rate > 0 else None

    return ConsumptionForecast(
        supplement_id=supplement.id,
        name=supplement.name,
        unit=supplement.unit,
        daily_rate=daily_rate,
        days_remaining=days_remaining,
    )


def forecast_all(
    manager: SupplementManager,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> list[ConsumptionForecast]:
    """Forecast remaining stock days for every supplement.

    Supplements with no intake history in the lookback window get
    ``days_remaining=None`` (not enough data) and are sorted to the end;
    the rest are sorted by soonest depletion first.
    """
    supplements = manager.list_supplements()
    logs = manager.get_calendar(days=lookback_days, limit=1_000_000)

    logs_by_supplement: dict[int, list[IntakeLog]] = {}
    for log in logs:
        logs_by_supplement.setdefault(log.supplement_id, []).append(log)

    forecasts = [
        estimate_consumption_rate(supplement, logs_by_supplement.get(supplement.id, []))
        for supplement in supplements
    ]

    def sort_key(forecast: ConsumptionForecast) -> tuple[int, float]:
        if forecast.days_remaining is None:
            return (1, 0.0)
        return (0, forecast.days_remaining)

    return sorted(forecasts, key=sort_key)


def get_daily_briefing(
    manager: SupplementManager,
    expiry_warning_days: int = 30,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> DailyBriefing:
    """Build a one-shot "what should I do today" summary.

    Combines stock/expiry alerts, consumption forecasts, and which
    supplements have (or have not) been logged yet today.
    """
    alerts = manager.check_alerts(expiry_warning_days)
    forecasts = forecast_all(manager, lookback_days=lookback_days)

    supplements = manager.list_supplements()
    today = date.today()
    taken_today = [
        log for log in manager.get_calendar(days=1, limit=1_000_000) if log.taken_at.date() == today
    ]
    taken_ids = {log.supplement_id for log in taken_today}
    pending_today = [supplement for supplement in supplements if supplement.id not in taken_ids]

    return DailyBriefing(
        alerts=alerts,
        forecasts=forecasts,
        taken_today=taken_today,
        pending_today=pending_today,
    )
