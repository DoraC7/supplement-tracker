"""Private, single-owner PostgreSQL storage; never falls back to SQLite.

Apply deployment/schema.sql separately as a dedicated migration owner. The
credential LOGIN role (created outside this repository) must be allowed to SET
ROLE supplemind_app. Do not expose the supplemind schema through a data API.
Use one Tracker per request/thread, not a shared connection. psycopg 3 is loaded
only when this backend is constructed. Dates and event snapshots remain text.

This is deliberately a small DB-API bridge for repository.py and tracker.py,
not a general SQLite dialect translator. It supports their SELECT/INSERT/UPDATE/
DELETE statements, qmark parameters, and transaction idioms, but no runtime DDL,
scripts, PRAGMAs, savepoints, or arbitrary INSERT targets. Nested Python contexts
share one transaction (not savepoints); any failure aborts the entire operation.
"""

from __future__ import annotations

import importlib
import re
import sqlite3
from collections import deque
from typing import Optional

from .errors import SupplementError
from .models import IntakeLog, Supplement
from .tracker import Tracker, quantity


class PostgresStorageError(SupplementError):
    """Safe to display; never includes a DSN, SQL, parameters, or driver text."""


class PostgresIntegrityError(sqlite3.IntegrityError, SupplementError):
    """Preserve legacy SQLite catches while remaining a domain exception."""


_FAILURE = "無法完成資料庫操作，請稍後再試或聯絡管理員。"
# One fixed, database-wide key used by every adapter transaction/process.
_STOCK_LOCK_KEY = 0x535550504C454D49
_IDENTITY_TABLES = {"supplements", "logs", "conflicts", "doseevents"}
_INSERT_TABLES = _IDENTITY_TABLES | {"trackerprofiles"}
# Existing queries use ordinary SQL strings/identifiers and SQL comments only.
# Reject unsupported quoting rather than risk silently translating its contents.
_SQL_PARTS = re.compile(r"'(?:(?:'')|[^'])*'|\"(?:(?:\"\")|[^\"])*\"|--[^\n]*(?:\n|$)|/\*.*?\*/", re.S)


def _translate(sql: str, parameter_count: int) -> tuple[str, bool, bool]:
    """Translate only unquoted qmarks; classify a single supported statement."""
    if not isinstance(sql, str):
        raise PostgresStorageError(_FAILURE)
    # Blank quoted/comment regions for classification and statement validation.
    code = _SQL_PARTS.sub(lambda match: " " * len(match.group()), sql)
    if any(character in code for character in "'\"$`\\"):
        raise PostgresStorageError(_FAILURE)
    statement = code.strip()
    if statement.endswith(";"):
        statement = statement[:-1].rstrip()
    if ";" in statement or not statement:
        raise PostgresStorageError(_FAILURE)
    verb = statement.split()[0].upper()
    if verb not in {"SELECT", "INSERT", "UPDATE", "DELETE"}:
        raise PostgresStorageError(_FAILURE)
    returning_id = False
    if verb == "INSERT":
        target = re.match(r"INSERT\s+INTO\s+([a-zA-Z_]+)\s*\(", statement, re.I)
        if not target or target[1].lower() not in _INSERT_TABLES:
            raise PostgresStorageError(_FAILURE)
        if re.search(r"\bRETURNING\b", statement, re.I):
            raise PostgresStorageError(_FAILURE)
        returning_id = target[1].lower() in _IDENTITY_TABLES
    if code.count("?") != parameter_count:
        raise PostgresStorageError(_FAILURE)

    pieces = []
    end = 0
    for match in _SQL_PARTS.finditer(sql):
        pieces.append(sql[end:match.start()].replace("%", "%%").replace("?", "%s"))
        pieces.append(match.group().replace("%", "%%"))
        end = match.end()
    pieces.append(sql[end:].replace("%", "%%").replace("?", "%s"))
    translated = "".join(pieces)
    # Remove a trailing statement terminator even if followed by a comment.
    semicolon = code.rfind(";")
    if semicolon != -1:
        # Work on original SQL then translate again to keep character offsets.
        return _translate(sql[:semicolon] + sql[semicolon + 1:], parameter_count)
    if returning_id:
        translated += "\nRETURNING id"
    return translated, returning_id, verb != "SELECT"


def _safe_error(exc: Exception) -> SupplementError:
    sqlstate = str(getattr(exc, "sqlstate", ""))
    if sqlstate.startswith("23"):
        message = "UNIQUE constraint failed" if sqlstate == "23505" else "Database integrity constraint failed"
        return PostgresIntegrityError(message)
    return PostgresStorageError(_FAILURE)


class _Cursor:
    """Materialize small Tracker result sets and promptly close native cursors."""

    def __init__(self, connection: _Connection):
        self.connection = connection
        self.lastrowid = None
        self.rowcount = -1
        self._rows = deque()

    def execute(self, sql: str, parameters=()):
        self._rows.clear()
        self.rowcount = -1
        if sql.strip().rstrip(";").upper() == "BEGIN IMMEDIATE":
            if parameters:
                raise PostgresStorageError(_FAILURE)
            self.connection._begin()
            return self
        translated, returning_id, write = _translate(sql, len(parameters))
        self.connection._check()
        if write:
            self.connection._begin()
        try:
            with self.connection._raw.cursor() as cursor:
                # Always pass a tuple: psycopg then unescapes literal %% too.
                cursor.execute(translated, tuple(parameters))
                self.rowcount = cursor.rowcount
                if returning_id:
                    row = cursor.fetchone()
                    self.lastrowid = row["id"] if row is not None else None
                elif cursor.description is not None:
                    self._rows.extend(cursor.fetchall())
        except Exception as exc:
            self.connection._abort()
            raise _safe_error(exc) from None
        return self

    def fetchone(self):
        return self._rows.popleft() if self._rows else None

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def __iter__(self):
        while self._rows:
            yield self._rows.popleft()

    def close(self):
        self._rows.clear()


class _Connection:
    """Explicit transactions on a psycopg autocommit connection.

    Writes outside a context start a transaction until explicit commit/rollback,
    like the original SQLite connection. Contexts lock before the first read.
    Inner commit calls are deferred so inherited restock cannot commit early.
    """

    def __init__(self, raw):
        self._raw = raw
        self._active = False
        self._depth = 0
        self._failed = False
        self._closed = False

    def _check(self):
        if self._closed or self._failed:
            raise PostgresStorageError(_FAILURE)

    def _abort(self):
        self._failed = self._depth > 0
        try:
            self._raw.rollback()
        except Exception:
            # Never reuse a connection whose transaction state is unknown.
            self._closed = True
            try:
                self._raw.close()
            except Exception:
                pass
        finally:
            self._active = False

    def _begin(self):
        self._check()
        if self._active:
            return
        try:
            with self._raw.cursor() as cursor:
                cursor.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
                self._active = True
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_STOCK_LOCK_KEY,))
        except Exception as exc:
            self._abort()
            raise _safe_error(exc) from None

    def cursor(self):
        self._check()
        return _Cursor(self)

    def execute(self, sql: str, parameters=()):
        return self.cursor().execute(sql, parameters)

    def commit(self):
        self._check()
        if self._depth or not self._active:
            return
        try:
            self._raw.commit()
        except Exception as exc:
            self._abort()
            raise _safe_error(exc) from None
        self._active = False

    def rollback(self):
        if self._closed:
            return
        self._failed = self._depth > 0
        try:
            self._raw.rollback()
        except Exception as exc:
            self._abort()
            raise _safe_error(exc) from None
        finally:
            self._active = False

    def __enter__(self):
        self._begin()
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if exc_type is not None:
            self._abort()
            return False
        if self._failed:
            if not self._depth:
                self._failed = False
            raise PostgresStorageError(_FAILURE)
        if not self._depth:
            self.commit()
        return False

    def close(self):
        if self._closed:
            return
        try:
            if self._active:
                self._raw.rollback()
            self._raw.close()
        except Exception as exc:
            self._abort()
            try:
                self._raw.close()
            except Exception:
                pass
            raise _safe_error(exc) from None
        finally:
            self._closed = True
            self._active = False


class PostgresTracker(Tracker):
    """Full Tracker API backed only by the provisioned private schema.

    DSN is required and never retained as a public attribute. TLS is mandatory;
    sslmode=require encrypts transport but does not verify server identity. Use
    trusted/private networking and provider certificate controls as appropriate.
    Credentials, role membership, schema deployment, and data API configuration
    are operator responsibilities, not performed by this constructor.
    """

    def __init__(self, dsn: str):
        raw = None
        try:
            if not isinstance(dsn, str) or not dsn.strip():
                raise ValueError("Missing configuration")
            psycopg = importlib.import_module("psycopg")
            rows = importlib.import_module("psycopg.rows")
            raw = psycopg.connect(
                dsn,
                sslmode="require",
                options="-c search_path=supplemind,pg_catalog",
                autocommit=True,
                prepare_threshold=None,
                connect_timeout=10,
                row_factory=rows.dict_row,
            )
            # LOGIN role needs membership with SET permission, not ownership.
            with raw.cursor() as cursor:
                cursor.execute("SET ROLE supplemind_app")
        except Exception:
            if raw is not None:
                try:
                    raw.close()
                except Exception:
                    pass
            raise PostgresStorageError(_FAILURE) from None
        # Intentionally bypass both Tracker and SupplementManager SQLite init.
        self.db_path = "postgresql:supplemind"
        self.conn = _Connection(raw)
        self.cursor = self.conn.cursor()

    def restock_supplement(self, supp_id: int, amount: float) -> Supplement:
        with self.conn:
            return super().restock_supplement(supp_id, amount)

    def take_supplement(
        self, supp_id: int, dosage: float, taken_at: Optional[str] = None,
    ) -> IntakeLog:
        # Legacy implementation checks stock BEFORE entering its own context.
        with self.conn:
            return super().take_supplement(supp_id, dosage, taken_at)

    @staticmethod
    def _validate_positive_number(value: float, field_name: str) -> float:
        return quantity(value, positive=True)

    @staticmethod
    def _validate_non_negative_number(value: float, field_name: str) -> float:
        return quantity(value)