"""Data models and shared formatting constants for SuppleMind."""

from dataclasses import dataclass
from datetime import date, datetime

DB_DATE_FORMAT = "%Y-%m-%d"
DB_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Supplement:
    id: int
    name: str
    unit: str
    stock_quantity: float
    warning_level: float
    expiry_date: date


@dataclass(frozen=True)
class IntakeLog:
    id: int
    supplement_id: int
    name: str
    taken_at: datetime
    dosage: float
    unit: str


@dataclass(frozen=True)
class Alert:
    level: str
    name: str
    message: str
