"""Offline adapter tests plus opt-in, isolated PostgreSQL validation.

Default tests need neither psycopg nor a server. The SQLite-backed fake models
only DB-API interactions; it does NOT prove PostgreSQL semantics.

SUPPLEMIND_TEST_POSTGRES_DSN opts into real adapter tests using an ALREADY
provisioned, disposable TLS PostgreSQL database and psycopg 3. No runtime DDL or
automatic provisioning is done there. These tests write UUID-named fixtures and
delete only their own rows. Never point this variable at production.

SUPPLEMIND_TEST_LOCAL_POSTGRES=1 separately opts into schema/ACL tests using local
initdb/pg_ctl/psql binaries, no driver. A fresh local Unix-socket-only server is
started in a temporary directory and shut down afterward. No cloud calls.
"""

from __future__ import annotations

import importlib
import os
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import traceback
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from supplemind.errors import InsufficientStockError, SupplementError, ValidationError
from supplemind.postgres import (
    PostgresIntegrityError,
    PostgresStorageError,
    PostgresTracker,
    _STOCK_LOCK_KEY,
    _translate,
)
from supplemind.tracker import Tracker


class DriverError(Exception):
    def __init__(self, sqlstate="08006"):
        super().__init__("secret-password postgresql://private-user@private-host/database")
        self.sqlstate = sqlstate


class FakeRaw:
    """Small real-SQL fake, with traces/faults for transaction assertions."""

    def __init__(self):
        self.sqlite_tracker = Tracker(":memory:")
        self.db = self.sqlite_tracker.conn
        self.db.isolation_level = None
        self.trace = []
        self.fail = None
        self.open_cursors = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.trace.append("COMMIT")
        if self.fail == "COMMIT":
            raise DriverError()
        self.db.commit()

    def rollback(self):
        self.trace.append("ROLLBACK")
        if self.fail == "ROLLBACK":
            raise DriverError()
        self.db.rollback()

    def close(self):
        self.trace.append("CLOSE")
        self.closed = True
        self.db.close()


class FakeCursor:
    def __init__(self, raw):
        self.raw = raw
        self.cursor = raw.db.cursor()
        self.description = None
        self.rowcount = -1

    def __enter__(self):
        self.raw.open_cursors += 1
        return self

    def __exit__(self, *args):
        self.raw.open_cursors -= 1
        self.cursor.close()

    def execute(self, sql, parameters=()):
        normalized = " ".join(sql.split())
        self.raw.trace.append(normalized)
        if self.raw.fail and self.raw.fail in normalized:
            raise DriverError()
        if normalized == "SET ROLE supplemind_app":
            return
        if "pg_advisory_xact_lock" in normalized:
            assert parameters == (_STOCK_LOCK_KEY,)
            return
        if normalized.startswith("BEGIN"):
            sql = "BEGIN"
        # Sufficient for the existing Tracker SQL, not a PostgreSQL emulator.
        sql = sql.replace("%s", "?").replace("%%", "%")
        try:
            self.cursor.execute(sql, parameters)
        except sqlite3.IntegrityError as exc:
            state = "23505" if "UNIQUE constraint failed" in str(exc) else "23514"
            raise DriverError(state) from None
        self.description = self.cursor.description
        self.rowcount = self.cursor.rowcount

    def fetchone(self):
        row = self.cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self):
        return [dict(row) for row in self.cursor.fetchall()]


class TranslationTests(unittest.TestCase):
    def test_only_real_placeholders_are_translated_and_percent_is_escaped(self):
        sql, identity, write = _translate(
            "SELECT '?', 'it''s ? 50%', \"?\" FROM Logs -- ?\nWHERE id=? /* ? */;", 1,
        )
        self.assertIn("'?", sql)
        self.assertIn("'it''s ? 50%%'", sql)
        self.assertIn('"?"', sql)
        self.assertIn("-- ?", sql)
        self.assertIn("id=%s /* ? */", sql)
        self.assertFalse(identity)
        self.assertFalse(write)

    def test_identity_targets_and_profile_upsert(self):
        for table in ("Supplements", "Logs", "Conflicts", "DoseEvents"):
            with self.subTest(table=table):
                sql, identity, write = _translate(f"INSERT INTO {table}(name) VALUES (?); -- end", 1)
                self.assertTrue(identity and write)
                self.assertTrue(sql.endswith("\nRETURNING id"))
        sql, identity, write = _translate(
            "INSERT INTO TrackerProfiles(supplement_id, profile) VALUES (?, ?) "
            "ON CONFLICT(supplement_id) DO UPDATE SET profile=excluded.profile", 2,
        )
        self.assertFalse(identity)
        self.assertTrue(write)
        self.assertNotIn("RETURNING", sql)

    def test_narrow_dialect_fails_closed(self):
        for sql in (
            "CREATE TABLE stolen(id int)", "PRAGMA foreign_keys=ON", "DROP TABLE Logs",
            "SELECT 1; DELETE FROM Logs", "INSERT INTO public.Logs(id) VALUES (?)",
            "SELECT $$?$$", "SELECT 'unterminated", "SELECT ?",
            "INSERT INTO Logs(id) VALUES (1) RETURNING id",
        ):
            with self.subTest(sql=sql), self.assertRaises(PostgresStorageError):
                _translate(sql, 0)


class OfflineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.raw = FakeRaw()
        self.calls = []
        self.row_factory = object()

        def connect(*args, **kwargs):
            self.calls.append((args, kwargs))
            return self.raw

        modules = {
            "psycopg": SimpleNamespace(connect=connect),
            "psycopg.rows": SimpleNamespace(dict_row=self.row_factory),
        }
        with patch("supplemind.postgres.importlib.import_module", side_effect=modules.__getitem__), \
                patch.object(Tracker, "__init__", side_effect=AssertionError("SQLite init")):
            self.manager = PostgresTracker("postgresql://secret-password@private-host/test")

    def tearDown(self):
        self.manager.close()

    def add(self, **overrides):
        values = dict(name="測試 ? 50%", unit="粒", stock=10, warning=2,
                      expiry=(date.today() + timedelta(days=365)).isoformat(),
                      profile={"frequency": "每天", "slots": [{"time": "08:00", "dose": 1}]})
        values.update(overrides)
        return self.manager.save_item(**values)

    def test_constructor_forces_tls_private_search_path_and_no_ddl(self):
        self.assertIsInstance(self.manager, Tracker)
        args, options = self.calls[0]
        self.assertEqual(options, dict(
            sslmode="require", options="-c search_path=supplemind,pg_catalog",
            autocommit=True, prepare_threshold=None, connect_timeout=10,
            row_factory=self.row_factory,
        ))
        self.assertEqual(self.raw.trace, ["SET ROLE supplemind_app"])
        self.assertNotIn("secret-password", self.manager.db_path)
        self.assertEqual(self.raw.open_cursors, 0)

    def test_full_schedule_record_undo_edit_archive_and_snapshot(self):
        item = self.add()
        event_id = self.manager.record(item.id, "08:00", "taken", 1, "  note ? %  ")
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 9)
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "08:00", "taken", 1)
        self.add(supplement_id=item.id, name="新名稱", stock=100)
        event = self.manager.events(date.today(), date.today())[0]
        self.assertEqual((event["name"], event["note"]), (item.name, "note ? %"))
        self.manager.undo(event_id)
        event = self.manager.history(date.today(), date.today())[0]
        self.assertEqual(event["status"], "undone")
        self.assertIsNone(event["log_id"])
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.assertEqual(self.manager.get_calendar(), [])
        with self.assertRaises(ValidationError):
            self.manager.undo(event_id)
        self.manager.record(item.id, "08:00", "skipped", 1)
        self.manager.archive(item.id)
        self.assertEqual(self.manager.schedule(), [])
        self.assertEqual(len(self.manager.items(archived=True)), 1)
        self.manager.archive(item.id, False)
        self.assertEqual(len(self.manager.schedule()), 1)
        self.assertEqual(self.raw.open_cursors, 0)

    def test_as_needed_repeats_and_insufficient_stock_rolls_back(self):
        item = self.add(stock=2, profile={"frequency": "需要時"})
        self.manager.record(item.id, "as_needed", "taken", 1)
        self.manager.record(item.id, "as_needed", "taken", 1)
        with self.assertRaises(InsufficientStockError):
            self.manager.record(item.id, "as_needed", "taken", 1)
        self.assertEqual(len(self.manager.events(date.today(), date.today())), 2)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 0)

    def test_legacy_api_conflicts_alerts_and_text_date_history(self):
        first = self.manager.add_supplement("A", "粒", 10, 2, "2099-01-01")
        second = self.manager.add_supplement("B", "粒", 0, 2, "2099-01-01")
        log = self.manager.take_supplement(first.id, 2)
        self.assertEqual(log.supplement_id, first.id)
        self.assertEqual(self.manager.get_calendar(supplement_id=first.id)[0], log)
        self.assertEqual(self.manager.history(date.today(), date.today())[0]["slot"], "legacy")
        self.assertEqual(self.manager.profile(first.id)["frequency"], "尚未設定")
        conflict = self.manager.add_conflict(second.id, first.id, "avoid")
        self.assertEqual(self.manager.list_conflicts(), [conflict])
        self.assertEqual(len(self.manager.check_alerts()), 1)
        with self.assertRaises(ValidationError):
            self.manager.add_conflict(first.id, second.id)
        self.manager.remove_conflict(conflict.id)
        self.assertEqual(self.manager.list_conflicts(), [])

    def test_restock_and_legacy_take_lock_before_read_and_commit_once(self):
        item = self.add()
        for operation in (
            lambda: self.manager.restock_supplement(item.id, 2),
            lambda: self.manager.take_supplement(item.id, 1),
        ):
            self.raw.trace.clear()
            operation()
            self.assertEqual(self.raw.trace[0], "BEGIN ISOLATION LEVEL READ COMMITTED")
            self.assertEqual(self.raw.trace[1], "SELECT pg_advisory_xact_lock(%s)")
            self.assertTrue(self.raw.trace[2].startswith("SELECT"))
            self.assertEqual(self.raw.trace.count("COMMIT"), 1)
            self.assertGreater(self.raw.trace.index("COMMIT"), next(
                index for index, sql in enumerate(self.raw.trace) if sql.startswith("UPDATE")
            ))
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 11)

    def test_failure_after_stock_update_rolls_back_everything_and_hides_details(self):
        item = self.add()
        self.raw.fail = "INSERT INTO DoseEvents"
        with self.assertRaises(PostgresStorageError) as caught:
            self.manager.record(item.id, "08:00", "taken", 1)
        rendered = "".join(traceback.format_exception(caught.exception))
        self.assertNotIn("secret-password", rendered)
        self.assertNotIn("private-host", rendered)
        self.raw.fail = None
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.assertEqual(self.manager.get_calendar(), [])
        self.assertEqual(self.manager.events(date.today(), date.today()), [])

    def test_profile_failure_does_not_leave_partial_item(self):
        self.raw.fail = "INSERT INTO TrackerProfiles"
        with self.assertRaises(PostgresStorageError):
            self.add()
        self.raw.fail = None
        self.assertEqual(self.manager.list_supplements(), [])

    def test_explicit_transactions_and_all_implicit_writes_use_same_lock(self):
        item = self.add()
        self.raw.trace.clear()
        self.manager.conn.execute("BEGIN IMMEDIATE")
        self.manager.conn.execute("UPDATE Supplements SET stock_quantity=0 WHERE id=?", (item.id,))
        self.manager.conn.rollback()
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.assertEqual(self.raw.trace.count("SELECT pg_advisory_xact_lock(%s)"), 1)
        self.raw.trace.clear()
        self.manager.conn.execute("UPDATE Supplements SET stock_quantity=7 WHERE id=?", (item.id,))
        self.assertEqual(self.raw.trace[:2], ["BEGIN ISOLATION LEVEL READ COMMITTED",
                                             "SELECT pg_advisory_xact_lock(%s)"])
        self.manager.conn.commit()
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 7)

    def test_restocks_and_legacy_intakes_rollback_on_failure(self):
        item = self.add()
        self.raw.fail = "UPDATE Supplements"
        for operation in (self.manager.restock_supplement, self.manager.take_supplement):
            with self.assertRaises(PostgresStorageError):
                operation(item.id, 2)
        self.raw.fail = None
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.assertEqual(self.manager.get_calendar(), [])

    def test_unique_translation_preserves_legacy_catches_and_recovery(self):
        self.add()
        with self.assertRaises(ValidationError):
            self.add()
        self.assertEqual(len(self.manager.list_supplements()), 1)
        with self.assertRaises(ValidationError):
            self.manager.add_supplement("測試 ? 50%", "粒", 1, 0, "2099-01-01")
        self.add(name="second")
        self.assertEqual(len(self.manager.list_supplements()), 2)

    def test_check_violation_is_sanitized_domain_integrity_error(self):
        item = self.add()
        with self.assertRaises(PostgresIntegrityError) as caught:
            self.manager.conn.execute("UPDATE Supplements SET stock_quantity=? WHERE id=?", (-1, item.id))
        self.assertIsInstance(caught.exception, (SupplementError, sqlite3.IntegrityError))
        self.assertNotIn("UNIQUE", str(caught.exception))
        self.assertNotIn("secret-password", str(caught.exception))
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)

    def test_nonfinite_legacy_values_rejected_before_writes(self):
        item = self.add()
        for value in (float("inf"), float("-inf"), float("nan")):
            for operation in (self.manager.restock_supplement, self.manager.take_supplement):
                with self.subTest(value=value, operation=operation), self.assertRaises(ValidationError):
                    operation(item.id, value)
            with self.assertRaises(ValidationError):
                self.manager.add_supplement("bad", "粒", value, 0, "2099-01-01")
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)

    def test_nested_context_defers_commit_and_aborts_caught_inner_failure(self):
        item = self.add()
        with self.assertRaises(PostgresStorageError):
            with self.manager.conn:
                try:
                    with self.manager.conn:
                        self.manager.conn.execute("UPDATE Supplements SET stock_quantity=0 WHERE id=?", (item.id,))
                        raise ValueError("cancel")
                except ValueError:
                    pass
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)

    def test_commit_failure_rollback_and_lock_failure(self):
        item = self.add()
        self.raw.fail = "COMMIT"
        with self.assertRaises(PostgresStorageError):
            self.manager.restock_supplement(item.id, 2)
        self.raw.fail = None
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)
        self.raw.fail = "pg_advisory_xact_lock"
        with self.assertRaises(PostgresStorageError):
            self.manager.restock_supplement(item.id, 2)
        self.assertEqual(self.raw.trace[-1], "ROLLBACK")

    def test_failed_rollback_closes_connection(self):
        self.manager.conn.execute("BEGIN IMMEDIATE")
        self.raw.fail = "ROLLBACK"
        with self.assertRaises(PostgresStorageError):
            self.manager.conn.rollback()
        self.assertTrue(self.raw.closed)
        with self.assertRaises(PostgresStorageError):
            self.manager.list_supplements()

    def test_lastrowid_survives_select_and_connection_context_does_not_close(self):
        item = self.manager.add_supplement("legacy", "粒", 10, 0, "2099-01-01")
        self.assertEqual(self.manager.cursor.lastrowid, item.id)
        with self.manager.conn:
            cursor = self.manager.conn.execute("SELECT * FROM Supplements")
            self.assertEqual(cursor.fetchone()["id"], item.id)
            self.assertIsNone(cursor.fetchone())
        self.assertFalse(self.raw.closed)
        self.assertEqual(self.raw.open_cursors, 0)


class InitializationFailureTests(unittest.TestCase):
    def test_connect_failure_is_generic_and_empty_dsn_does_not_use_environment(self):
        modules = {"psycopg": SimpleNamespace(connect=lambda *a, **k: (_ for _ in ()).throw(DriverError())),
                   "psycopg.rows": SimpleNamespace(dict_row=object())}
        with patch.object(importlib, "import_module", side_effect=modules.__getitem__):
            with self.assertRaises(PostgresStorageError) as caught:
                PostgresTracker("configured")
        self.assertNotIn("secret-password", "".join(traceback.format_exception(caught.exception)))
        with patch.object(importlib, "import_module", side_effect=AssertionError("Must not load driver")):
            with self.assertRaises(PostgresStorageError):
                PostgresTracker("")

    def test_no_driver_no_fallback_no_credentials_in_displayed_traceback(self):
        with patch.object(sqlite3, "connect", side_effect=AssertionError("No fallback allowed")), \
                patch.object(importlib, "import_module", side_effect=ImportError("secret-password")):
            with self.assertRaises(PostgresStorageError) as caught:
                PostgresTracker("postgresql://secret-password@private-host/db")
        self.assertNotIn("secret-password", "".join(traceback.format_exception(caught.exception)))

    def test_failed_role_setup_closes_raw_connection(self):
        raw = FakeRaw()
        raw.fail = "SET ROLE"
        modules = {"psycopg": SimpleNamespace(connect=lambda *a, **k: raw),
                   "psycopg.rows": SimpleNamespace(dict_row=object())}
        with patch("supplemind.postgres.importlib.import_module", side_effect=modules.__getitem__):
            with self.assertRaises(PostgresStorageError):
                PostgresTracker("configured")
        self.assertTrue(raw.closed)


@unittest.skipUnless(os.environ.get("SUPPLEMIND_TEST_POSTGRES_DSN"), "No opt-in PostgreSQL DSN")
class LiveAdapterTests(unittest.TestCase):
    """Uses only UUID-scoped data in a pre-provisioned disposable TLS database."""

    def setUp(self):
        # Import happens only after opt-in, never during test collection.
        try:
            importlib.import_module("psycopg")
        except ImportError:
            self.skipTest("psycopg 3 not installed")
        self.dsn = os.environ["SUPPLEMIND_TEST_POSTGRES_DSN"]
        self.manager = PostgresTracker(self.dsn)
        self.ids = []
        self.addCleanup(self.manager.close)
        self.addCleanup(self.cleanup_rows)

    def cleanup_rows(self):
        with self.manager.conn:
            for item_id in self.ids:
                self.manager.conn.execute("DELETE FROM DoseEvents WHERE supplement_id=?", (item_id,))
                self.manager.conn.execute("DELETE FROM TrackerProfiles WHERE supplement_id=?", (item_id,))
                self.manager.conn.execute("DELETE FROM Supplements WHERE id=?", (item_id,))

    def add(self, stock=10, scheduled=False):
        profile = ({"frequency": "每天", "slots": [{"time": "08:00", "dose": 1}]}
                   if scheduled else {"frequency": "需要時"})
        item = self.manager.save_item(name=f"pg-test-{uuid4()}", unit="粒", stock=stock,
                                      warning=1, expiry="2099-01-01", profile=profile)
        self.ids.append(item.id)
        return item

    def test_snapshot_undo_history_and_legacy_api(self):
        item = self.add(scheduled=True)
        event_id = self.manager.record(item.id, "08:00", "taken", 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 9)
        with self.assertRaises(ValidationError):
            self.manager.record(item.id, "08:00", "taken", 1)
        self.manager.undo(event_id)
        self.manager.take_supplement(item.id, 2)
        history = [row for row in self.manager.history(date.today(), date.today())
                   if row["supplement_id"] == item.id]
        self.assertEqual({row["slot"] for row in history}, {"legacy", "08:00"})
        self.assertEqual(self.manager.restock_supplement(item.id, 2).stock_quantity, 10)
        self.manager.archive(item.id)
        self.assertTrue(self.manager.profile(item.id)["archived"])

    def test_concurrent_legacy_take_and_restock_no_lost_updates(self):
        item = self.add(stock=1)
        barrier = threading.Barrier(3)

        def operate(action):
            with PostgresTracker(self.dsn) as tracker:
                barrier.wait(timeout=15)
                try:
                    if action == "take":
                        tracker.take_supplement(item.id, 1)
                    else:
                        tracker.restock_supplement(item.id, 1)
                    return action
                except InsufficientStockError:
                    return "insufficient"

        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(operate, ["take", "take", "restock"]))
        expected_stock = 2 - results.count("take")
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, expected_stock)
        self.assertEqual(len(self.manager.get_calendar(supplement_id=item.id)), results.count("take"))

    def test_concurrent_duplicate_doses_and_undo_are_serialized(self):
        item = self.add(scheduled=True)

        def race(operation):
            barrier = threading.Barrier(2)

            def run(_):
                with PostgresTracker(self.dsn) as tracker:
                    barrier.wait(timeout=15)
                    try:
                        return operation(tracker)
                    except ValidationError:
                        return "rejected"

            with ThreadPoolExecutor(max_workers=2) as pool:
                return list(pool.map(run, range(2)))

        results = race(lambda tracker: tracker.record(item.id, "08:00", "taken", 1))
        self.assertEqual(results.count("rejected"), 1)
        event_id = next(result for result in results if result != "rejected")
        results = race(lambda tracker: tracker.undo(event_id))
        self.assertEqual(results.count("rejected"), 1)
        self.assertEqual(self.manager.get_supplement(item.id).stock_quantity, 10)


@unittest.skipUnless(os.environ.get("SUPPLEMIND_TEST_LOCAL_POSTGRES") == "1", "Local server tests not enabled")
class LocalSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.binaries = {name: shutil.which(name) for name in ("initdb", "pg_ctl", "psql")}
        if not all(cls.binaries.values()):
            raise unittest.SkipTest("Local PostgreSQL binaries not available")
        cls.temp = tempfile.TemporaryDirectory(prefix="supplemind-pg-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.data = cls.root / "data"
        cls.socket = cls.root / "socket"
        cls.socket.mkdir()
        # No cloud host/environment inherited; unique local Unix socket only.
        cls.env = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        subprocess.run([cls.binaries["initdb"], "-D", str(cls.data), "-U", "postgres",
                        "--auth=trust", "--no-locale", "--encoding=UTF8"],
                       env=cls.env, check=True, capture_output=True, text=True)
        subprocess.run([cls.binaries["pg_ctl"], "-D", str(cls.data), "-l", str(cls.root / "server.log"),
                        "-o", f"-k {cls.socket} -c listen_addresses='' -c fsync=off", "-w", "start"],
                       env=cls.env, check=True, capture_output=True, text=True)
        cls.addClassCleanup(cls.stop_server)
        cls.sql("CREATE ROLE anon; CREATE ROLE authenticated;")
        schema = Path(__file__).resolve().parents[1] / "deployment" / "schema.sql"
        cls.sql(schema.read_text())

    @classmethod
    def stop_server(cls):
        subprocess.run([cls.binaries["pg_ctl"], "-D", str(cls.data), "-m", "fast", "-w", "stop"],
                       env=cls.env, check=True, capture_output=True, text=True)

    @classmethod
    def sql(cls, sql, *, succeeds=True):
        result = subprocess.run([cls.binaries["psql"], "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                                 "-h", str(cls.socket), "-U", "postgres", "-d", "postgres"],
                                input=sql, env=cls.env, capture_output=True, text=True)
        if succeeds and result.returncode:
            raise AssertionError(result.stderr)
        if not succeeds and not result.returncode:
            raise AssertionError("Expected PostgreSQL to reject statement")
        return result.stdout.strip()

    def test_acl_roles_defaults_and_no_ddl_for_runtime_role(self):
        self.assertEqual(self.sql("SELECT rolcanlogin, rolinherit, rolbypassrls FROM pg_roles WHERE rolname='supplemind_app'"), "f|f|f")
        self.sql("CREATE ROLE test_login LOGIN NOINHERIT; GRANT supplemind_app TO test_login;")
        self.assertEqual(self.sql("SET SESSION AUTHORIZATION test_login; SET ROLE supplemind_app; SELECT current_user;"), "supplemind_app")
        for role in ("anon", "authenticated"):
            self.assertEqual(self.sql(f"SELECT has_schema_privilege('{role}', 'supplemind', 'USAGE')"), "f")
            self.assertEqual(self.sql(f"SELECT has_table_privilege('{role}', 'supplemind.supplements', 'SELECT')"), "f")
            self.assertEqual(self.sql(f"SELECT has_sequence_privilege('{role}', 'supplemind.supplements_id_seq', 'USAGE')"), "f")
            self.sql(f"SET ROLE {role}; SELECT * FROM supplemind.Supplements;", succeeds=False)
        self.sql("SET ROLE supplemind_app; CREATE TABLE supplemind.forbidden(id int);", succeeds=False)
        self.sql("SET ROLE supplemind_app; TRUNCATE supplemind.Supplements CASCADE;", succeeds=False)
        self.sql("CREATE TABLE supplemind.future_acl_test(id BIGINT GENERATED ALWAYS AS IDENTITY);")
        self.assertEqual(self.sql("SELECT has_table_privilege('supplemind_app', 'supplemind.future_acl_test', 'SELECT,INSERT,UPDATE,DELETE')"), "t")
        self.assertEqual(self.sql("SELECT has_sequence_privilege('supplemind_app', 'supplemind.future_acl_test_id_seq', 'USAGE')"), "t")
        for role in ("anon", "authenticated"):
            self.assertEqual(self.sql(f"SELECT has_table_privilege('{role}', 'supplemind.future_acl_test', 'SELECT')"), "f")

    def test_finite_stock_doses_partial_index_and_text_snapshots(self):
        prefix = "SET ROLE supplemind_app; SET search_path=supplemind,pg_catalog; "
        item_id = self.sql(prefix + "INSERT INTO Supplements(name,unit,stock_quantity,warning_level,expiry_date,created_at,updated_at) "
                           "VALUES ('schema-test','粒',10,0,'2099-01-01','2026-09-26 08:00:00','2026-09-26 08:00:00') RETURNING id;")
        for column in ("stock_quantity", "warning_level"):
            for value in ("NaN", "Infinity", "-Infinity", "-1"):
                self.sql(prefix + f"UPDATE Supplements SET {column}='{value}' WHERE id={item_id};", succeeds=False)
        for table, column, extra_columns, extra_values in (
            ("Logs", "dosage", "taken_at", "'2026-09-26 08:00:00'"),
            ("DoseEvents", "dose", "day,slot,status,name,unit,recorded_at",
             "'2026-09-26','as_needed','taken','snapshot','粒','2026-09-26 08:00:00'"),
        ):
            for value in ("NaN", "Infinity", "-Infinity", "0", "-1"):
                self.sql(prefix + f"INSERT INTO {table}(supplement_id,{column},{extra_columns}) "
                         f"VALUES ({item_id},'{value}',{extra_values});", succeeds=False)
        insert = (f"INSERT INTO DoseEvents(supplement_id,day,slot,status,dose,name,unit,recorded_at) "
                  f"VALUES ({item_id},'2026-09-26','08:00','taken',1,'snapshot','粒','2026-09-26 08:00:00');")
        self.sql(prefix + insert)
        self.sql(prefix + insert, succeeds=False)
        self.sql(prefix + f"UPDATE DoseEvents SET status='undone' WHERE supplement_id={item_id};" + insert)
        self.sql(prefix + insert.replace("'08:00'", "'as_needed'") * 2)
        self.assertEqual(self.sql(prefix + "SELECT pg_typeof(day), pg_typeof(recorded_at), pg_typeof(id), pg_typeof(dose) FROM DoseEvents LIMIT 1;"), "text|text|bigint|double precision")
        self.sql(prefix + f"INSERT INTO TrackerProfiles(supplement_id,profile,archived) VALUES ({item_id},'{{}}',2);", succeeds=False)

    def test_log_deletion_retains_event_snapshot_and_nulls_reference(self):
        prefix = "SET ROLE supplemind_app; SET search_path=supplemind,pg_catalog; "
        item_id = self.sql(prefix + "INSERT INTO Supplements(name,unit,stock_quantity,warning_level,expiry_date,created_at,updated_at) "
                           "VALUES ('fk-test','粒',10,0,'2099-01-01','2026-09-26 08:00:00','2026-09-26 08:00:00') RETURNING id;")
        log_id = self.sql(prefix + f"INSERT INTO Logs(supplement_id,taken_at,dosage) VALUES ({item_id},'2026-09-26 08:00:00',1) RETURNING id;")
        self.sql(prefix + "INSERT INTO DoseEvents(supplement_id,day,slot,status,dose,name,unit,recorded_at,log_id) "
                 f"VALUES ({item_id},'2026-09-26','08:00','undone',1,'snapshot','粒','2026-09-26 08:00:00',{log_id});")
        self.sql(prefix + f"DELETE FROM Logs WHERE id={log_id};")
        self.assertEqual(self.sql(prefix + f"SELECT log_id IS NULL, name, recorded_at FROM DoseEvents WHERE supplement_id={item_id};"),
                         "t|snapshot|2026-09-26 08:00:00")


if __name__ == "__main__":
    unittest.main()