"""Command-line interface: argument parsing and command dispatch.

This module only knows how to translate CLI args into calls on
``SupplementManager`` and how to print the results. All business logic lives
in ``repository.py``; all table rendering lives in ``formatting.py``.
"""

import argparse
from typing import Optional

from .errors import SupplementError
from .formatting import (
    format_quantity,
    print_alerts,
    print_conflicts,
    print_forecasts,
    print_history,
    print_supplements,
    print_today_plan,
)
from .models import DB_DATE_FORMAT, DB_DATETIME_FORMAT, Supplement
from .repository import DEFAULT_DB_PATH, SupplementManager
from .services import DEFAULT_LOOKBACK_DAYS, forecast_all, get_today_plan


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Supplement inventory and intake tracker")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help="SQLite database path (default: health_tracker.db)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add a new supplement")
    add_parser.add_argument("--name", required=True, help="Supplement name")
    add_parser.add_argument("--unit", required=True, help="Inventory unit, e.g. 粒, 包, 滴")
    add_parser.add_argument("--stock", required=True, type=float, help="Current stock quantity")
    add_parser.add_argument("--warning", required=True, type=float, help="Low stock warning level")
    add_parser.add_argument("--expiry", required=True, help="Expiry date in YYYY-MM-DD")

    list_parser = subparsers.add_parser("list", help="List all supplements")
    list_parser.add_argument(
        "--sort",
        choices=("expiry", "name", "stock"),
        default="expiry",
        help="Display sort order",
    )

    take_parser = subparsers.add_parser("take", help="Log intake and reduce stock")
    take_parser.add_argument("--id", required=True, type=int, help="Supplement id")
    take_parser.add_argument("--dosage", required=True, type=float, help="Dosage quantity")
    take_parser.add_argument(
        "--taken-at",
        help="Override intake time in YYYY-MM-DD HH:MM:SS",
    )

    restock_parser = subparsers.add_parser("restock", help="Increase supplement stock")
    restock_parser.add_argument("--id", required=True, type=int, help="Supplement id")
    restock_parser.add_argument("--amount", required=True, type=float, help="Restock amount")

    alerts_parser = subparsers.add_parser("alerts", help="Show stock and expiry alerts")
    alerts_parser.add_argument(
        "--days",
        default=30,
        type=int,
        help="Warn when expiry is within this many days",
    )

    history_parser = subparsers.add_parser("history", help="Show intake history")
    history_parser.add_argument("--days", default=7, type=int, help="Number of days to look back")
    history_parser.add_argument("--limit", default=50, type=int, help="Maximum rows to display")
    history_parser.add_argument("--id", type=int, help="Filter by supplement id")

    detail_parser = subparsers.add_parser("detail", help="Show one supplement")
    detail_parser.add_argument("--id", required=True, type=int, help="Supplement id")

    forecast_parser = subparsers.add_parser(
        "forecast", help="Estimate consumption rate and days of stock remaining"
    )
    forecast_parser.add_argument(
        "--lookback-days",
        default=DEFAULT_LOOKBACK_DAYS,
        type=int,
        help="How many days of intake history to use for the estimate",
    )

    today_parser = subparsers.add_parser(
        "today",
        help=(
            "Today's decision loop: what to take, what not to stack, "
            "what's checked off, what's running low"
        ),
    )
    today_parser.add_argument(
        "--days",
        default=30,
        type=int,
        help="Warn when expiry is within this many days",
    )

    conflict_parser = subparsers.add_parser(
        "conflict", help="Manage which supplements should not be taken on the same day"
    )
    conflict_subparsers = conflict_parser.add_subparsers(dest="conflict_action", required=True)

    conflict_add_parser = conflict_subparsers.add_parser(
        "add", help="Mark two supplements as not to be stacked on the same day"
    )
    conflict_add_parser.add_argument("--id-a", required=True, type=int, help="First supplement id")
    conflict_add_parser.add_argument("--id-b", required=True, type=int, help="Second supplement id")
    conflict_add_parser.add_argument(
        "--note", default="", help="Optional note, e.g. why they conflict"
    )

    conflict_subparsers.add_parser("list", help="List conflict rules")

    conflict_remove_parser = conflict_subparsers.add_parser("remove", help="Remove a conflict rule")
    conflict_remove_parser.add_argument("--id", required=True, type=int, help="Conflict rule id")

    return parser


def _sorted_supplements(supplements: list[Supplement], sort_key: str) -> list[Supplement]:
    if sort_key == "name":
        return sorted(supplements, key=lambda item: item.name.lower())
    if sort_key == "stock":
        return sorted(supplements, key=lambda item: (item.stock_quantity, item.name.lower()))
    return sorted(supplements, key=lambda item: (item.expiry_date, item.name.lower()))


def run_cli(args: argparse.Namespace) -> int:
    try:
        with SupplementManager(args.db) as manager:
            if args.command == "add":
                supplement = manager.add_supplement(
                    name=args.name,
                    unit=args.unit,
                    stock=args.stock,
                    warning_level=args.warning,
                    expiry_date=args.expiry,
                )
                print(
                    f"已新增 {supplement.name}，庫存 {format_quantity(supplement.stock_quantity)}"
                    f"{supplement.unit}，到期日 {supplement.expiry_date.strftime(DB_DATE_FORMAT)}。"
                )
                return 0

            if args.command == "list":
                supplements = _sorted_supplements(manager.list_supplements(), args.sort)
                print_supplements(supplements)
                return 0

            if args.command == "take":
                log = manager.take_supplement(
                    supp_id=args.id,
                    dosage=args.dosage,
                    taken_at=args.taken_at,
                )
                updated = manager.get_supplement(args.id)
                print(
                    f"已記錄 {log.name} 服用 {format_quantity(log.dosage)}{log.unit}，"
                    f"時間 {log.taken_at.strftime(DB_DATETIME_FORMAT)}。"
                )
                print(f"剩餘庫存：{format_quantity(updated.stock_quantity)}{updated.unit}")
                return 0

            if args.command == "restock":
                supplement = manager.restock_supplement(args.id, args.amount)
                print(
                    f"已補貨 {supplement.name} {format_quantity(args.amount)}{supplement.unit}，"
                    f"目前庫存 {format_quantity(supplement.stock_quantity)}{supplement.unit}。"
                )
                return 0

            if args.command == "alerts":
                print_alerts(manager.check_alerts(args.days))
                return 0

            if args.command == "history":
                logs = manager.get_calendar(days=args.days, limit=args.limit, supplement_id=args.id)
                print_history(logs)
                return 0

            if args.command == "detail":
                print_supplements([manager.get_supplement(args.id)])
                return 0

            if args.command == "forecast":
                print_forecasts(forecast_all(manager, lookback_days=args.lookback_days))
                return 0

            if args.command == "today":
                plan = get_today_plan(manager, expiry_warning_days=args.days)
                print_today_plan(plan)
                return 0

            if args.command == "conflict":
                if args.conflict_action == "add":
                    conflict = manager.add_conflict(args.id_a, args.id_b, note=args.note)
                    print(
                        f"已設定「{conflict.name_a}」與「{conflict.name_b}」不能同天服用。"
                    )
                    return 0

                if args.conflict_action == "list":
                    print_conflicts(manager.list_conflicts())
                    return 0

                if args.conflict_action == "remove":
                    manager.remove_conflict(args.id)
                    print(f"已移除疊加規則 id {args.id}。")
                    return 0

    except SupplementError as exc:
        print(f"錯誤：{exc}")
        return 1

    raise AssertionError(f"Unhandled command: {args.command}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_cli(args)
