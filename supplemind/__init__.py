"""SuppleMind: a supplement inventory and intake tracker.

Package layout:
    errors.py      - domain-specific exceptions
    models.py      - dataclasses shared across layers (Supplement, IntakeLog, Alert)
    repository.py  - SQLite data access layer (SupplementManager)
    formatting.py  - console table/text rendering helpers
    cli.py         - argparse wiring and command dispatch

The names below are re-exported at the package root for backward
compatibility with code (and tests) that used to do
``from supplemind import SupplementManager`` when this was a single file.
"""

from .cli import build_parser, main, run_cli
from .errors import (
    InsufficientStockError,
    SupplementError,
    SupplementNotFoundError,
    ValidationError,
)
from .models import DB_DATE_FORMAT, DB_DATETIME_FORMAT, Alert, IntakeLog, Supplement
from .repository import DEFAULT_DB_PATH, SupplementManager
from .services import (
    DEFAULT_LOOKBACK_DAYS,
    ConsumptionForecast,
    DailyBriefing,
    estimate_consumption_rate,
    forecast_all,
    get_daily_briefing,
)

__all__ = [
    "SupplementError",
    "ValidationError",
    "SupplementNotFoundError",
    "InsufficientStockError",
    "Supplement",
    "IntakeLog",
    "Alert",
    "DB_DATE_FORMAT",
    "DB_DATETIME_FORMAT",
    "SupplementManager",
    "DEFAULT_DB_PATH",
    "ConsumptionForecast",
    "DailyBriefing",
    "DEFAULT_LOOKBACK_DAYS",
    "estimate_consumption_rate",
    "forecast_all",
    "get_daily_briefing",
    "build_parser",
    "run_cli",
    "main",
]
