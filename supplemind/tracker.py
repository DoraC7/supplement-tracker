"""Scheduled-dose tracking for the web app, preserving the original CLI tables.

Schedules apply prospectively. Events retain dose/name/time snapshots even when a
schedule is edited. Legacy Logs are never assigned to an invented schedule.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date, datetime

from .errors import InsufficientStockError, ValidationError
from .repository import SupplementManager

COLORS = {"薄荷綠": "#128875", "海洋藍": "#3478C7", "薰衣草": "#8563BE", "杏桃橘": "#C97A41", "玫瑰粉": "#BE627D"}
FORMS = ["膠囊", "錠劑", "粉包", "液體", "軟糖", "其他"]
FREQUENCIES = ["尚未設定", "每天", "指定星期", "需要時"]


def quantity(value: float, positive: bool = False) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValidationError("請輸入有效數字。") from exc
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        raise ValidationError("用量必須大於 0，庫存與警戒值不能小於 0，且必須是有限數字。")
    return result


def validate_profile(profile: dict) -> dict:
    """Validate and normalize user-authored metadata and schedule."""
    frequency = profile.get("frequency", "尚未設定")
    if frequency not in FREQUENCIES:
        raise ValidationError("不支援的排程類型。")
    form = profile.get("form", "膠囊")
    color = profile.get("color", "薄荷綠")
    if form not in FORMS or color not in COLORS:
        raise ValidationError("請選擇有效的外觀。")
    weekdays = sorted(set(profile.get("weekdays", list(range(7)))))
    if any(type(day) is not int or day not in range(7) for day in weekdays):
        raise ValidationError("星期設定無效。")
    if frequency == "指定星期" and not weekdays:
        raise ValidationError("請至少選擇一個星期。")
    slots = []
    for slot in profile.get("slots", []):
        clock = slot.get("time", "")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
            raise ValidationError("時間格式須為 HH:MM，例如 08:00。")
        slots.append({"time": clock, "dose": quantity(slot["dose"], positive=True)})
    if len({slot["time"] for slot in slots}) != len(slots):
        raise ValidationError("同一保健品不可設定重複時間。")
    if frequency in ("每天", "指定星期") and not slots:
        raise ValidationError("請至少設定一個服用時間。")
    start = date.fromisoformat(profile.get("start", date.today().isoformat()))
    end = date.fromisoformat(profile["end"]) if profile.get("end") else None
    if end and end < start:
        raise ValidationError("結束日期不能早於開始日期。")
    return {
        "frequency": frequency, "form": form, "color": color,
        "strength": str(profile.get("strength", "")).strip(),
        "notes": str(profile.get("notes", "")).strip(),
        "weekdays": weekdays, "slots": sorted(slots, key=lambda slot: slot["time"]),
        "start": start.isoformat(), "end": end.isoformat() if end else None,
        "default_dose": quantity(profile.get("default_dose", 1), positive=True),
    }


class Tracker(SupplementManager):
    """One connection per page run; each dose and stock change is atomic."""

    def __init__(self, db_name="health_tracker.db"):
        super().__init__(db_name)
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS TrackerProfiles (
                supplement_id INTEGER PRIMARY KEY REFERENCES Supplements(id),
                profile TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1))
            );
            CREATE TABLE IF NOT EXISTS DoseEvents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                supplement_id INTEGER NOT NULL REFERENCES Supplements(id),
                day TEXT NOT NULL,
                slot TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('taken', 'skipped', 'undone')),
                dose REAL NOT NULL CHECK(dose > 0),
                name TEXT NOT NULL,
                unit TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                log_id INTEGER REFERENCES Logs(id) ON DELETE SET NULL,
                undone_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS unique_active_dose
                ON DoseEvents(supplement_id, day, slot)
                WHERE status != 'undone' AND slot != 'as_needed';
        """)

    def profile(self, supplement_id: int) -> dict:
        row = self.conn.execute(
            "SELECT profile, archived FROM TrackerProfiles WHERE supplement_id = ?", (supplement_id,)
        ).fetchone()
        if row:
            return {**json.loads(row["profile"]), "archived": bool(row["archived"])}
        return {**validate_profile({}), "archived": False}

    def items(self, archived: bool = False) -> list:
        return [item for item in self.list_supplements() if self.profile(item.id)["archived"] == archived]

    def save_item(self, *, name, unit, stock, warning, expiry, profile, supplement_id=None):
        name = self._validate_text(name, "名稱")
        unit = self._validate_text(unit, "單位")
        stock, warning = quantity(stock), quantity(warning)
        expiry = self._parse_date(expiry).isoformat()
        normalized = validate_profile(profile)
        now = self._now_string()
        try:
            with self.conn:
                if supplement_id is None:
                    cursor = self.conn.execute(
                        """INSERT INTO Supplements
                        (name, unit, stock_quantity, warning_level, expiry_date, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (name, unit, stock, warning, expiry, now, now),
                    )
                    supplement_id = cursor.lastrowid
                else:
                    existing = self.get_supplement(supplement_id)
                    if unit != existing.unit:
                        raise ValidationError("建立後不能更改庫存單位，請封存後新增另一項。")
                    # Do not replace stock with a stale form value. Restock is separate.
                    self.conn.execute(
                        """UPDATE Supplements SET name=?, warning_level=?, expiry_date=?, updated_at=?
                        WHERE id=?""", (name, warning, expiry, now, supplement_id),
                    )
                self.conn.execute(
                    """INSERT INTO TrackerProfiles(supplement_id, profile) VALUES (?, ?)
                    ON CONFLICT(supplement_id) DO UPDATE SET profile=excluded.profile""",
                    (supplement_id, json.dumps(normalized, ensure_ascii=False)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValidationError("已有同名保健品，請使用不同名稱。") from exc
        return self.get_supplement(supplement_id)

    def archive(self, supplement_id: int, archived: bool = True) -> None:
        self.get_supplement(supplement_id)
        profile = self.profile(supplement_id)
        profile.pop("archived")
        with self.conn:
            self.conn.execute(
                """INSERT INTO TrackerProfiles(supplement_id, profile, archived) VALUES (?, ?, ?)
                ON CONFLICT(supplement_id) DO UPDATE SET archived=excluded.archived""",
                (supplement_id, json.dumps(profile, ensure_ascii=False), int(archived)),
            )

    def schedule(self, day: date | None = None) -> list[dict]:
        day = day or date.today()
        events = {(event["supplement_id"], event["slot"]): event for event in self.events(day, day)
                  if event["status"] != "undone"}
        result = []
        for item in self.items():
            profile = self.profile(item.id)
            if profile["frequency"] not in ("每天", "指定星期"):
                continue
            if day.isoformat() < profile["start"] or (profile["end"] and day.isoformat() > profile["end"]):
                continue
            if profile["frequency"] == "指定星期" and day.weekday() not in profile["weekdays"]:
                continue
            for slot in profile["slots"]:
                result.append({"item": item, "profile": profile, **slot,
                               "event": events.get((item.id, slot["time"]))})
        return sorted(result, key=lambda entry: (entry["time"], entry["item"].name))

    def record(self, supplement_id: int, slot: str, status: str, dose: float, note: str = "") -> int:
        if status not in ("taken", "skipped"):
            raise ValidationError("記錄狀態無效。")
        dose = quantity(dose, positive=True)
        day = date.today().isoformat()
        now = self._now_string()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            item = self.get_supplement(supplement_id)
            profile = self.profile(supplement_id)
            if profile["archived"]:
                raise ValidationError("已封存的保健品不能新增記錄。")
            if day < profile["start"] or (profile["end"] and day > profile["end"]):
                raise ValidationError("今天不在此保健品設定的服用日期範圍內。")
            if slot == "as_needed":
                if profile["frequency"] != "需要時" or status != "taken":
                    raise ValidationError("此項目不是需要時服用。")
            else:
                matches = [entry for entry in self.schedule() if entry["item"].id == supplement_id and entry["time"] == slot]
                if not matches or matches[0]["event"]:
                    raise ValidationError("此劑已記錄或排程已變更，請重新整理。")
                if dose != matches[0]["dose"]:
                    raise ValidationError("用量已更新，請重新整理後記錄。")
            log_id = None
            if status == "taken":
                if item.expiry_date < date.today():
                    raise ValidationError("此保健品已過期，不能記錄為已服用；請確認或更換產品。")
                cursor = self.conn.execute(
                    """UPDATE Supplements SET stock_quantity=stock_quantity-?, updated_at=?
                    WHERE id=? AND stock_quantity>=?""", (dose, now, supplement_id, dose),
                )
                if cursor.rowcount != 1:
                    raise InsufficientStockError("庫存不足，請先確認並補貨。")
                cursor = self.conn.execute(
                    "INSERT INTO Logs(supplement_id, taken_at, dosage) VALUES (?, ?, ?)",
                    (supplement_id, now, dose),
                )
                log_id = cursor.lastrowid
            cursor = self.conn.execute(
                """INSERT INTO DoseEvents
                (supplement_id, day, slot, status, dose, name, unit, recorded_at, note, log_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (supplement_id, day, slot, status, dose, item.name, item.unit, now, note.strip(), log_id),
            )
            self.conn.commit()
            return cursor.lastrowid
        except Exception:
            self.conn.rollback()
            raise

    def undo(self, event_id: int) -> None:
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            event = self.conn.execute("SELECT * FROM DoseEvents WHERE id=?", (event_id,)).fetchone()
            if not event or event["status"] == "undone":
                raise ValidationError("此記錄不存在或已撤銷。")
            if event["status"] == "taken":
                self.conn.execute(
                    "UPDATE Supplements SET stock_quantity=stock_quantity+?, updated_at=? WHERE id=?",
                    (event["dose"], self._now_string(), event["supplement_id"]),
                )
                # Keep the immutable DoseEvents snapshot as an audit record.
                self.conn.execute("DELETE FROM Logs WHERE id=?", (event["log_id"],))
            self.conn.execute(
                "UPDATE DoseEvents SET status='undone', undone_at=? WHERE id=?",
                (self._now_string(), event_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def events(self, start: date, end: date) -> list[dict]:
        return [dict(row) for row in self.conn.execute(
            "SELECT * FROM DoseEvents WHERE day BETWEEN ? AND ? ORDER BY recorded_at DESC, id DESC",
            (start.isoformat(), end.isoformat()),
        ).fetchall()]

    def history(self, start: date, end: date) -> list[dict]:
        events = self.events(start, end)
        legacy = [dict(row) for row in self.conn.execute(
            """SELECT Logs.id, Logs.supplement_id, Supplements.name, Supplements.unit,
            Logs.taken_at AS recorded_at, Logs.dosage AS dose, 'taken' AS status,
            'legacy' AS slot, '' AS note
            FROM Logs JOIN Supplements ON Supplements.id=Logs.supplement_id
            WHERE date(Logs.taken_at) BETWEEN ? AND ?
            AND NOT EXISTS(SELECT 1 FROM DoseEvents WHERE DoseEvents.log_id=Logs.id)""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()]
        return sorted(events + legacy, key=lambda event: event["recorded_at"], reverse=True)