"""Console output helpers: table rendering and printing of domain objects.

Kept separate from ``cli.py`` so the presentation logic can be unit tested
(or swapped, e.g. for JSON output) without touching argument parsing.
"""

from typing import Iterable

from .models import DB_DATE_FORMAT, DB_DATETIME_FORMAT, Alert, IntakeLog, Supplement
from .services import ConsumptionForecast, DailyBriefing


def format_quantity(value: float) -> str:
    return f"{value:g}"


def render_table(headers: list[str], rows: Iterable[Iterable[object]]) -> str:
    string_rows = [[str(cell) for cell in row] for row in rows]
    widths = [len(header) for header in headers]
    for row in string_rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def format_row(row: Iterable[str]) -> str:
        return " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(row))

    divider = "-+-".join("-" * width for width in widths)
    lines = [format_row(headers), divider]
    lines.extend(format_row(row) for row in string_rows)
    return "\n".join(lines)


def print_supplements(supplements: list[Supplement]) -> None:
    if not supplements:
        print("目前沒有任何保健品資料。")
        return

    rows = [
        [
            supp.id,
            supp.name,
            format_quantity(supp.stock_quantity),
            supp.unit,
            format_quantity(supp.warning_level),
            supp.expiry_date.strftime(DB_DATE_FORMAT),
        ]
        for supp in supplements
    ]
    print(render_table(["ID", "名稱", "庫存", "單位", "警戒值", "到期日"], rows))


def print_history(logs: list[IntakeLog]) -> None:
    if not logs:
        print("指定條件下沒有服用紀錄。")
        return

    rows = [
        [
            log.id,
            log.supplement_id,
            log.name,
            log.taken_at.strftime(DB_DATETIME_FORMAT),
            format_quantity(log.dosage),
            log.unit,
        ]
        for log in logs
    ]
    print(render_table(["Log ID", "Supp ID", "名稱", "服用時間", "劑量", "單位"], rows))


def print_alerts(alerts: list[Alert]) -> None:
    if not alerts:
        print("目前沒有需要提醒的項目。")
        return

    rows = [[alert.level, alert.name, alert.message] for alert in alerts]
    print(render_table(["等級", "名稱", "訊息"], rows))


def print_forecasts(forecasts: list[ConsumptionForecast]) -> None:
    if not forecasts:
        print("目前沒有任何保健品資料可估算消耗速度。")
        return

    rows = []
    for forecast in forecasts:
        if forecast.days_remaining is None:
            rate_text = "資料不足"
            remaining_text = "資料不足"
        else:
            rate_text = f"{forecast.daily_rate:.2f}{forecast.unit}/天"
            remaining_text = f"{forecast.days_remaining:.1f} 天"
        rows.append([forecast.supplement_id, forecast.name, rate_text, remaining_text])

    print(render_table(["ID", "名稱", "平均消耗速度", "預估剩餘天數"], rows))


def print_daily_briefing(briefing: DailyBriefing) -> None:
    print("== 今日提醒（庫存/效期）==")
    print_alerts(briefing.alerts)

    print()
    print("== 消耗速度預估 ==")
    print_forecasts(briefing.forecasts)

    print()
    print(f"== 今天已記錄服用（{len(briefing.taken_today)} 筆）==")
    print_history(briefing.taken_today)

    print()
    print(f"== 今天尚未記錄服用（{len(briefing.pending_today)} 項）==")
    print_supplements(briefing.pending_today)
