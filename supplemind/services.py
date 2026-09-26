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
DEFAULT_EXPIRY_WARNING_DAYS = 30


@dataclass(frozen=True)
class ConsumptionForecast:
    supplement_id: int
    name: str
    unit: str
    daily_rate: float
    days_remaining: Optional[float]  # None when there is not enough history


@dataclass(frozen=True)
class ConflictAlert:
    """A human-readable warning that two supplements should not be stacked today."""

    name_a: str
    name_b: str
    note: str
    message: str


@dataclass(frozen=True)
class TodayPlan:
    """The daily decision loop: what to take, what not to stack, what's done, what's low.

    Field order mirrors the loop the app is meant to answer every day:
    1. ``to_take``          - 今天用什麼 (not yet logged today)
    2. ``conflict_alerts``  - 哪些不能疊 (don't stack these together today)
    3. ``taken``            - 用完打勾 (already logged today, via the `take` command)
    4. ``low_stock_alerts`` - 快用完再提醒 (running low, restock soon)
    """

    to_take: list[Supplement]
    conflict_alerts: list[ConflictAlert]
    taken: list[IntakeLog]
    low_stock_alerts: list[Alert]
    expiry_alerts: list[Alert]


@dataclass(frozen=True)
class DailyBriefing:
    """Dashboard-compatible summary backed by the current daily plan."""

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


def _build_conflict_alerts(
    manager: SupplementManager,
    taken_ids: set[int],
    pending_ids: set[int],
) -> list[ConflictAlert]:
    conflict_alerts: list[ConflictAlert] = []

    for conflict in manager.list_conflicts():
        a_id, b_id = conflict.supplement_id_a, conflict.supplement_id_b
        a_taken, b_taken = a_id in taken_ids, b_id in taken_ids
        a_pending, b_pending = a_id in pending_ids, b_id in pending_ids
        note_suffix = f"（{conflict.note}）" if conflict.note else ""

        if a_taken and b_pending:
            message = f"今天已服用「{conflict.name_a}」，先別再服用「{conflict.name_b}」{note_suffix}"
        elif b_taken and a_pending:
            message = f"今天已服用「{conflict.name_b}」，先別再服用「{conflict.name_a}」{note_suffix}"
        elif a_pending and b_pending:
            message = f"「{conflict.name_a}」與「{conflict.name_b}」不建議同天服用，今天只選一項{note_suffix}"
        elif a_taken and b_taken:
            message = f"今天已同時服用「{conflict.name_a}」與「{conflict.name_b}」，之後請錯開時段{note_suffix}"
        else:
            continue

        conflict_alerts.append(
            ConflictAlert(
                name_a=conflict.name_a,
                name_b=conflict.name_b,
                note=conflict.note,
                message=message,
            )
        )

    return conflict_alerts


def get_today_plan(
    manager: SupplementManager,
    expiry_warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS,
) -> TodayPlan:
    """Build today's decision loop: what to take, what not to stack, what's done, what's low.

    This is the app's single daily entry point: open it, see what's left to
    take today, see which pairs shouldn't be stacked, check off what's
    already logged, and see what's about to run out.
    """
    supplements = manager.list_supplements()
    today = date.today()
    taken_today = [
        log for log in manager.get_calendar(days=1, limit=1_000_000) if log.taken_at.date() == today
    ]
    taken_ids = {log.supplement_id for log in taken_today}
    to_take = [supplement for supplement in supplements if supplement.id not in taken_ids]
    pending_ids = {supplement.id for supplement in to_take}

    conflict_alerts = _build_conflict_alerts(manager, taken_ids, pending_ids)

    alerts = manager.check_alerts(expiry_warning_days)
    low_stock_alerts = [alert for alert in alerts if alert.level == "warning"]
    expiry_alerts = [alert for alert in alerts if alert.level != "warning"]

    return TodayPlan(
        to_take=to_take,
        conflict_alerts=conflict_alerts,
        taken=taken_today,
        low_stock_alerts=low_stock_alerts,
        expiry_alerts=expiry_alerts,
    )


def get_daily_briefing(
    manager: SupplementManager,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    expiry_warning_days: int = DEFAULT_EXPIRY_WARNING_DAYS,
) -> DailyBriefing:
    """Preserve the dashboard API while sharing the current daily-plan logic."""
    plan = get_today_plan(manager, expiry_warning_days=expiry_warning_days)
    return DailyBriefing(
        alerts=plan.low_stock_alerts + plan.expiry_alerts,
        forecasts=forecast_all(manager, lookback_days=lookback_days),
        taken_today=plan.taken,
        pending_today=plan.to_take,
    )
