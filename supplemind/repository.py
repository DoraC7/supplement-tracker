"""SQLite-backed data access layer for supplements and intake logs.

This module owns the database schema and all read/write operations. It does
not know anything about argparse or console output — that lives in
``supplemind.cli`` / ``supplemind.formatting``.
"""

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from .errors import (
    InsufficientStockError,
    SupplementError,
    SupplementNotFoundError,
    ValidationError,
)
from .models import DB_DATE_FORMAT, DB_DATETIME_FORMAT, Alert, IntakeLog, Supplement

DEFAULT_DB_PATH = Path("health_tracker.db")


class SupplementManager:
    def __init__(self, db_name: str | Path = DEFAULT_DB_PATH):
        self.db_path = str(db_name)
        # check_same_thread=False: this manager instance may be reused across
        # threads by long-lived callers that cache it (e.g. Streamlit reruns
        # each interaction on a fresh script-runner thread). SuppleMind is a
        # single-user, mostly-sequential tool, so cross-thread reuse of one
        # connection is safe here.
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._configure_connection()
        self._create_tables()

    def _configure_connection(self) -> None:
        self.cursor.execute("PRAGMA foreign_keys = ON")

    def _create_tables(self) -> None:
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS Supplements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                unit TEXT NOT NULL,
                stock_quantity REAL NOT NULL CHECK(stock_quantity >= 0),
                warning_level REAL NOT NULL CHECK(warning_level >= 0),
                expiry_date TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS Logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplement_id INTEGER NOT NULL,
                taken_at TEXT NOT NULL,
                dosage REAL NOT NULL CHECK(dosage > 0),
                FOREIGN KEY(supplement_id) REFERENCES Supplements(id) ON DELETE CASCADE
            )
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "SupplementManager":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def add_supplement(
        self,
        name: str,
        unit: str,
        stock: float,
        warning_level: float,
        expiry_date: str,
    ) -> Supplement:
        normalized_name = self._validate_text(name, "name")
        normalized_unit = self._validate_text(unit, "unit")
        stock_value = self._validate_non_negative_number(stock, "stock")
        warning_value = self._validate_non_negative_number(warning_level, "warning_level")
        expiry = self._parse_date(expiry_date)
        now = self._now_string()

        try:
            self.cursor.execute(
                """
                INSERT INTO Supplements (
                    name, unit, stock_quantity, warning_level, expiry_date, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_name,
                    normalized_unit,
                    stock_value,
                    warning_value,
                    expiry.strftime(DB_DATE_FORMAT),
                    now,
                    now,
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ValidationError(f"Supplement '{normalized_name}' already exists.") from exc
            raise

        return self.get_supplement(self.cursor.lastrowid)

    def list_supplements(self) -> list[Supplement]:
        rows = self.cursor.execute(
            """
            SELECT id, name, unit, stock_quantity, warning_level, expiry_date
            FROM Supplements
            ORDER BY expiry_date ASC, name ASC
            """
        ).fetchall()
        return [self._row_to_supplement(row) for row in rows]

    def get_supplement(self, supp_id: int) -> Supplement:
        row = self.cursor.execute(
            """
            SELECT id, name, unit, stock_quantity, warning_level, expiry_date
            FROM Supplements
            WHERE id = ?
            """,
            (self._validate_positive_int(supp_id, "supp_id"),),
        ).fetchone()
        if row is None:
            raise SupplementNotFoundError(f"Supplement id {supp_id} not found.")
        return self._row_to_supplement(row)

    def restock_supplement(self, supp_id: int, amount: float) -> Supplement:
        supplement = self.get_supplement(supp_id)
        increment = self._validate_positive_number(amount, "amount")
        self.cursor.execute(
            """
            UPDATE Supplements
            SET stock_quantity = stock_quantity + ?, updated_at = ?
            WHERE id = ?
            """,
            (increment, self._now_string(), supplement.id),
        )
        self.conn.commit()
        return self.get_supplement(supplement.id)

    def take_supplement(
        self,
        supp_id: int,
        dosage: float,
        taken_at: Optional[str] = None,
    ) -> IntakeLog:
        supplement = self.get_supplement(supp_id)
        dosage_value = self._validate_positive_number(dosage, "dosage")

        if dosage_value > supplement.stock_quantity:
            raise InsufficientStockError(
                f"Not enough stock for '{supplement.name}'. "
                f"Current stock: {supplement.stock_quantity:g}{supplement.unit}, "
                f"requested: {dosage_value:g}{supplement.unit}."
            )

        intake_time = self._parse_datetime(taken_at) if taken_at else datetime.now()

        with self.conn:
            self.cursor.execute(
                """
                INSERT INTO Logs (supplement_id, taken_at, dosage)
                VALUES (?, ?, ?)
                """,
                (
                    supplement.id,
                    intake_time.strftime(DB_DATETIME_FORMAT),
                    dosage_value,
                ),
            )
            log_id = self.cursor.lastrowid
            self.cursor.execute(
                """
                UPDATE Supplements
                SET stock_quantity = stock_quantity - ?, updated_at = ?
                WHERE id = ?
                """,
                (dosage_value, self._now_string(), supplement.id),
            )

        return self.get_log(log_id)

    def get_log(self, log_id: int) -> IntakeLog:
        row = self.cursor.execute(
            """
            SELECT
                Logs.id,
                Logs.supplement_id,
                Supplements.name,
                Logs.taken_at,
                Logs.dosage,
                Supplements.unit
            FROM Logs
            JOIN Supplements ON Logs.supplement_id = Supplements.id
            WHERE Logs.id = ?
            """,
            (self._validate_positive_int(log_id, "log_id"),),
        ).fetchone()
        if row is None:
            raise ValidationError(f"Log id {log_id} not found.")
        return self._row_to_log(row)

    def get_calendar(
        self,
        days: int = 7,
        limit: int = 50,
        supplement_id: Optional[int] = None,
    ) -> list[IntakeLog]:
        day_count = self._validate_positive_int(days, "days")
        row_limit = self._validate_positive_int(limit, "limit")
        cutoff = datetime.combine(date.today() - timedelta(days=day_count - 1), datetime.min.time())

        query = """
            SELECT
                Logs.id,
                Logs.supplement_id,
                Supplements.name,
                Logs.taken_at,
                Logs.dosage,
                Supplements.unit
            FROM Logs
            JOIN Supplements ON Logs.supplement_id = Supplements.id
            WHERE Logs.taken_at >= ?
        """
        params: list[object] = [cutoff.strftime(DB_DATETIME_FORMAT)]

        if supplement_id is not None:
            query += " AND Logs.supplement_id = ?"
            params.append(self._validate_positive_int(supplement_id, "supplement_id"))

        query += " ORDER BY Logs.taken_at DESC LIMIT ?"
        params.append(row_limit)

        rows = self.cursor.execute(query, params).fetchall()
        return [self._row_to_log(row) for row in rows]

    def check_alerts(self, expiry_warning_days: int = 30) -> list[Alert]:
        warning_window = self._validate_non_negative_int(expiry_warning_days, "expiry_warning_days")
        today = date.today()
        alerts: list[Alert] = []

        for supplement in self.list_supplements():
            if supplement.stock_quantity <= supplement.warning_level:
                alerts.append(
                    Alert(
                        level="warning",
                        name=supplement.name,
                        message=(
                            f"庫存偏低，剩餘 {supplement.stock_quantity:g}{supplement.unit}，"
                            f"警戒值為 {supplement.warning_level:g}{supplement.unit}。"
                        ),
                    )
                )

            days_to_expire = (supplement.expiry_date - today).days
            if days_to_expire < 0:
                alerts.append(
                    Alert(
                        level="expired",
                        name=supplement.name,
                        message=f"已於 {supplement.expiry_date.strftime(DB_DATE_FORMAT)} 過期。",
                    )
                )
            elif days_to_expire <= warning_window:
                alerts.append(
                    Alert(
                        level="expiry_soon",
                        name=supplement.name,
                        message=(
                            f"將於 {days_to_expire} 天後 "
                            f"({supplement.expiry_date.strftime(DB_DATE_FORMAT)}) 過期。"
                        ),
                    )
                )

        return alerts

    def _row_to_supplement(self, row: sqlite3.Row) -> Supplement:
        return Supplement(
            id=row["id"],
            name=row["name"],
            unit=row["unit"],
            stock_quantity=row["stock_quantity"],
            warning_level=row["warning_level"],
            expiry_date=self._parse_date(row["expiry_date"]),
        )

    def _row_to_log(self, row: sqlite3.Row) -> IntakeLog:
        return IntakeLog(
            id=row["id"],
            supplement_id=row["supplement_id"],
            name=row["name"],
            taken_at=self._parse_datetime(row["taken_at"]),
            dosage=row["dosage"],
            unit=row["unit"],
        )

    @staticmethod
    def _validate_text(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValidationError(f"{field_name} must be a string.")
        normalized = value.strip()
        if not normalized:
            raise ValidationError(f"{field_name} cannot be empty.")
        return normalized

    @staticmethod
    def _validate_positive_number(value: float, field_name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field_name} must be a number.") from exc
        if number <= 0:
            raise ValidationError(f"{field_name} must be greater than 0.")
        return number

    @staticmethod
    def _validate_non_negative_number(value: float, field_name: str) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field_name} must be a number.") from exc
        if number < 0:
            raise ValidationError(f"{field_name} cannot be negative.")
        return number

    @staticmethod
    def _validate_positive_int(value: int, field_name: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field_name} must be an integer.") from exc
        if number <= 0:
            raise ValidationError(f"{field_name} must be greater than 0.")
        return number

    @staticmethod
    def _validate_non_negative_int(value: int, field_name: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"{field_name} must be an integer.") from exc
        if number < 0:
            raise ValidationError(f"{field_name} cannot be negative.")
        return number

    @staticmethod
    def _parse_date(value: str) -> date:
        try:
            return datetime.strptime(value, DB_DATE_FORMAT).date()
        except ValueError as exc:
            raise ValidationError(
                f"Invalid date '{value}'. Use YYYY-MM-DD."
            ) from exc

    @staticmethod
    def _parse_datetime(value: str) -> datetime:
        try:
            return datetime.strptime(value, DB_DATETIME_FORMAT)
        except ValueError as exc:
            raise ValidationError(
                f"Invalid datetime '{value}'. Use YYYY-MM-DD HH:MM:SS."
            ) from exc

    @staticmethod
    def _now_string() -> str:
        return datetime.now().strftime(DB_DATETIME_FORMAT)
