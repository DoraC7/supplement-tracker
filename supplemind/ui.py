"""Local, personal supplement tracking interface."""
from __future__ import annotations

import csv
import io
import os
from datetime import date, datetime, time, timedelta
from html import escape
from pathlib import Path

import streamlit as st

from .errors import SupplementError
from .formatting import format_quantity as q
from .tracker import COLORS, FORMS, FREQUENCIES, Tracker, quantity

ROOT = Path(__file__).resolve().parent.parent
WEEK = ["一", "二", "三", "四", "五", "六", "日"]
STATUS = {"taken": "已服用", "skipped": "已略過", "undone": "已撤銷"}


def html(content):
    st.markdown(content, unsafe_allow_html=True)


def act(function, message, *args, **kwargs):
    try:
        function(*args, **kwargs)
    except (SupplementError, ValueError) as exc:
        st.error(str(exc))
    else:
        st.session_state["notice"] = message
        st.rerun()


def card(item, profile, detail=""):
    color = COLORS[profile["color"]]
    shape = "round" if profile["form"] in ("錠劑", "軟糖") else "capsule"
    html(f'<div class="item-heading"><div class="pill-icon" style="--pill:{color}"><span class="{shape}"></span></div>'
         f'<div><div class="item-title">{escape(item.name)}</div><div class="muted">{escape(profile["strength"] or profile["form"])}'
         f'{" · " + escape(detail) if detail else ""}</div></div></div>')


def schedule_label(profile):
    if profile["frequency"] in ("尚未設定", "需要時"):
        return profile["frequency"]
    days = "每天" if profile["frequency"] == "每天" else "週" + "、".join(WEEK[day] for day in profile["weekdays"])
    return days + " · " + " / ".join(slot["time"] for slot in profile["slots"])


def item_form(manager, item=None):
    key = str(item.id) if item else "new"
    profile = manager.profile(item.id) if item else {
        "form": "膠囊", "color": "薄荷綠", "strength": "", "notes": "", "frequency": "尚未設定",
        "slots": [], "weekdays": list(range(7)), "start": date.today().isoformat(), "end": None, "default_dose": 1,
    }
    st.caption("依保健品標示填寫用量與時間，建立自己的記錄計畫。")
    frequency = st.selectbox("服用頻率", FREQUENCIES, index=FREQUENCIES.index(profile["frequency"]), key=f"freq_{key}")
    scheduled = frequency in ("每天", "指定星期")
    count = st.number_input("每天幾個時段", min_value=1, max_value=8, value=max(1, len(profile["slots"])), step=1,
                            key=f"count_{key}") if scheduled else 0
    with st.form(f"form_{key}"):
        st.markdown("#### 基本資料")
        name = st.text_input("名稱 *", value=item.name if item else "", placeholder="例如：維他命 D3")
        c1, c2 = st.columns(2)
        form = c1.selectbox("類型", FORMS, index=FORMS.index(profile["form"]))
        strength = c2.text_input("每單位含量（選填）", value=profile["strength"], placeholder="例如：1000 IU")
        c1, c2 = st.columns(2)
        color = c1.selectbox("識別顏色", list(COLORS), index=list(COLORS).index(profile["color"]))
        unit = c2.text_input("庫存單位 *", value=item.unit if item else "粒", disabled=item is not None)
        st.markdown("#### 服用計畫")
        weekdays = st.multiselect("服用日", list(range(7)), default=profile["weekdays"],
                                 format_func=lambda day: "星期" + WEEK[day]) if frequency == "指定星期" else list(range(7))
        slots = []
        for index in range(int(count)):
            prior = profile["slots"][index] if index < len(profile["slots"]) else {"time": f"{(8 + index * 2) % 24:02d}:00", "dose": 1.0}
            c1, c2 = st.columns(2)
            clock = c1.time_input(f"時段 {index + 1}", value=time.fromisoformat(prior["time"]), key=f"clock_{key}_{index}")
            dose = c2.number_input(f"每次用量 {index + 1}（{unit}）", min_value=0.25, value=float(prior["dose"]), step=0.25, key=f"dose_{key}_{index}")
            slots.append({"time": clock.strftime("%H:%M"), "dose": dose})
        default_dose = st.number_input("需要時的預設用量", min_value=0.25, value=float(profile["default_dose"]), step=0.25) if frequency == "需要時" else profile["default_dose"]
        c1, c2 = st.columns(2)
        start = c1.date_input("排程開始日期", value=date.fromisoformat(profile["start"]))
        end = c2.date_input("排程結束日期（選填）", value=date.fromisoformat(profile["end"]) if profile["end"] else None)
        notes = st.text_area("服用備註（選填）", value=profile["notes"], placeholder="例如：依產品標示隨餐服用；個人的注意事項")
        st.markdown("#### 庫存與效期")
        c1, c2 = st.columns(2)
        stock = c1.number_input("目前庫存", min_value=0.0, value=float(item.stock_quantity) if item else 0.0, step=1.0, disabled=item is not None)
        warning = c2.number_input("低庫存提醒門檻", min_value=0.0, value=float(item.warning_level) if item else 5.0, step=1.0)
        expiry = st.date_input("到期日 *", value=item.expiry_date if item else date.today() + timedelta(days=365))
        if item:
            st.caption("庫存請用「補貨」調整。不同批次效期請另建一項，避免混用。")
            st.caption("排程修改會立即影響今日待辦，原有記錄仍保留。若今天已服用，請勿因變更時段再次服用。")
        if st.form_submit_button("儲存變更" if item else "加入我的保健品", type="primary", width="stretch"):
            act(manager.save_item, "保健品已儲存", name=name, unit=unit, stock=stock, warning=warning,
                expiry=expiry.isoformat(), supplement_id=item.id if item else None,
                profile={"form": form, "color": color, "strength": strength, "frequency": frequency,
                         "weekdays": weekdays, "slots": slots, "start": start.isoformat(),
                         "end": end.isoformat() if end else None, "notes": notes, "default_dose": default_dose})


def today_view(manager, cloud=False):
    today = date.today()
    timezone_label = "台北時間" if cloud else "本機時間"
    st.caption(f"{today.year} 年 {today.month} 月 {today.day} 日 · 星期{WEEK[today.weekday()]} · {timezone_label}")
    entries = manager.schedule()
    taken = sum(bool(entry["event"] and entry["event"]["status"] == "taken") for entry in entries)
    skipped = sum(bool(entry["event"] and entry["event"]["status"] == "skipped") for entry in entries)
    pending = len(entries) - taken - skipped
    total = len(entries)
    percent = round((taken + skipped) / total * 100) if total else 0
    html(f'<div class="hero"><div><div class="eyebrow">YOUR DAILY MOMENT</div>'
         f'<h2>{"今天的記錄都完成了" if total and not pending else "照顧自己，從今天開始"}</h2>'
         f'<p>{f"還有 {pending} 劑等待記錄，依你的計畫慢慢來。" if total else "加入你的保健品，建立適合自己的日常。"}</p>'
         f'<div class="hero-badges"><span>✓ 已服用 {taken}</span><span>↷ 已略過 {skipped}</span></div></div>'
         f'<div class="progress-ring" style="--progress:{percent}%"><div><strong>{taken + skipped}<small> / {total}</small></strong><span>已記錄</span></div></div></div>')
    html('<div class="section-title">今日記錄 <span>依服用時間排列</span></div>')
    if not entries:
        st.info("今天沒有排程。到「我的保健品」設定服用計畫，或在「新增」加入一項。")
    last_clock = None
    for entry in entries:
        item, profile, event = entry["item"], entry["profile"], entry["event"]
        if entry["time"] != last_clock:
            st.markdown(f"#### {entry['time']}")
            last_clock = entry["time"]
        with st.container(border=True):
            recorded_dose = event["dose"] if event else entry["dose"]
            card(item, profile, f"{q(recorded_dose)} {item.unit}")
            if profile["notes"]:
                st.caption(profile["notes"])
            if event:
                st.success(f"{STATUS[event['status']]} · {event['recorded_at'][11:16]}", icon="✅" if event["status"] == "taken" else "↪️")
                if st.button("撤銷這筆記錄", key=f"undo_{event['id']}"):
                    act(manager.undo, "已撤銷；若原為服用，庫存已回補", event["id"])
            else:
                expired = item.expiry_date < today
                insufficient = item.stock_quantity < entry["dose"]
                if expired:
                    st.error("已過期，請勿服用。請確認產品或更換後再設定。")
                elif insufficient:
                    st.warning("庫存不足，請先確認並補貨。")
                elif datetime.combine(today, time.fromisoformat(entry["time"])) + timedelta(minutes=30) < datetime.now():
                    st.caption("🕒 超過排程時間 30 分鐘，尚未記錄。請依實際情況選擇已服用或略過。")
                else:
                    st.caption("待記錄 · 請在實際服用後再點選")
                c1, c2 = st.columns(2)
                if c1.button("✓ 已服用", key=f"take_{item.id}_{entry['time']}", type="primary", width="stretch", disabled=expired or insufficient):
                    act(manager.record, "已記錄服用，庫存已更新", item.id, entry["time"], "taken", entry["dose"])
                if c2.button("略過", key=f"skip_{item.id}_{entry['time']}", width="stretch"):
                    act(manager.record, "已略過，庫存不變", item.id, entry["time"], "skipped", entry["dose"])
    needed = [item for item in manager.items() if manager.profile(item.id)["frequency"] == "需要時"]
    if needed:
        st.markdown("### 需要時服用")
        st.caption("不列入今日排程；只記錄實際發生的服用。")
        for item in needed:
            profile = manager.profile(item.id)
            with st.container(border=True):
                card(item, profile, f"庫存 {q(item.stock_quantity)} {item.unit}")
                dose = st.number_input("本次用量", min_value=0.25, value=float(profile["default_dose"]), step=0.25, key=f"prn_{item.id}")
                outside_dates = today.isoformat() < profile["start"] or bool(profile["end"] and today.isoformat() > profile["end"])
                if outside_dates:
                    st.caption("今天不在設定的服用日期範圍內。")
                if item.expiry_date < today:
                    st.error("已過期，請勿服用。")
                elif item.stock_quantity < dose:
                    st.warning("庫存不足。")
                if st.button("記錄本次服用", key=f"prn_take_{item.id}", disabled=outside_dates or item.expiry_date < today or item.stock_quantity < dose):
                    act(manager.record, "已記錄本次服用", item.id, "as_needed", "taken", dose)
    today_events = [event for event in manager.events(today, today) if event["status"] != "undone"]
    if today_events:
        with st.expander("今日所有記錄（含需要時與已變更的排程）"):
            for event in today_events:
                st.write(f"{event['recorded_at'][11:16]} · {event['name']} · {q(event['dose'])}{event['unit']} · {STATUS[event['status']]}")
                if st.button("撤銷", key=f"all_undo_{event['id']}"):
                    act(manager.undo, "已撤銷記錄並同步庫存", event["id"])
    unconfigured = [item.name for item in manager.items() if manager.profile(item.id)["frequency"] == "尚未設定"]
    if unconfigured:
        st.info("尚未設定排程：" + "、".join(unconfigured) + "。既有資料已保留，請至「我的保健品」編輯。")
    active_names = {item.name for item in manager.items()}
    alerts = [alert for alert in manager.check_alerts() if alert.name in active_names]
    if alerts:
        st.markdown("### 值得留意")
        for alert in alerts:
            (st.error if alert.level == "expired" else st.warning)(f"{alert.name} · {alert.message}")
    with st.expander("關於提醒與時間"):
        st.write(("雲端版固定使用台北時間（Asia/Taipei）。" if cloud else "所有時間依伺服器本機時區。") + "開啟頁面時每 60 秒更新記錄狀態；目前只有頁內提醒，沒有背景推播或鎖定畫面通知。旅行時請確認排程。")


def collection_view(manager):
    st.markdown("### 我的保健品")
    search = st.text_input("搜尋保健品", placeholder="搜尋名稱…", label_visibility="collapsed")
    archived = st.toggle("顯示已封存")
    items = [item for item in manager.items(archived) if search.casefold() in item.name.casefold()]
    st.caption(f"{len(items)} 項 · " + ("封存保留所有歷史記錄" if archived else "你的個人保健品清單"))
    if not items:
        st.info("沒有符合的保健品。你可以在「新增」建立一項。")
    for item in items:
        profile = manager.profile(item.id)
        with st.container(border=True):
            card(item, profile, schedule_label(profile))
            c1, c2, c3 = st.columns(3)
            c1.metric("目前庫存", f"{q(item.stock_quantity)} {item.unit}")
            c2.metric("提醒門檻", f"{q(item.warning_level)} {item.unit}")
            c3.metric("到期日", item.expiry_date.strftime("%Y/%m/%d"))
            if item.expiry_date < date.today():
                st.error("已過期 · 請更換產品，勿因記錄提醒而服用。")
            elif item.stock_quantity <= item.warning_level:
                st.warning("庫存偏低，記得確認是否需要補貨。")
            if profile["notes"]:
                st.caption(profile["notes"])
            if not archived:
                with st.expander("編輯詳細資訊與排程"):
                    item_form(manager, item)
                with st.expander("補貨"):
                    st.caption("僅適用同一批次／相同效期。不同效期請新增另一項。")
                    amount = st.number_input("增加數量", min_value=0.0, value=0.0, step=1.0, key=f"stock_{item.id}")
                    if st.button("確認補貨", key=f"restock_{item.id}"):
                        try:
                            quantity(amount, positive=True)
                        except SupplementError as exc:
                            st.error(str(exc))
                        else:
                            act(manager.restock_supplement, "庫存已增加", item.id, amount)
            if st.button("恢復使用（保留原排程）" if archived else "封存此項（保留歷史）", key=f"archive_{item.id}"):
                act(manager.archive, "已恢復，請確認原排程" if archived else "已封存，歷史記錄仍保留", item.id, not archived)


def csv_bytes(rows):
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value
                          for key, value in row.items()} for row in rows)
    return buffer.getvalue().encode("utf-8-sig")


def history_view(manager):
    st.markdown("### 每一次記錄，都值得留下")
    c1, c2 = st.columns(2)
    start = c1.date_input("開始日期", value=date.today() - timedelta(days=29), max_value=date.today())
    end = c2.date_input("結束日期", value=date.today(), max_value=date.today())
    if start > end:
        st.error("開始日期不能晚於結束日期。")
        return
    items = manager.list_supplements()
    names = {item.id: item.name for item in items}
    selected = st.selectbox("保健品", [None] + list(names), format_func=lambda sid: "全部保健品" if sid is None else names[sid])
    events = [event for event in manager.history(start, end) if selected is None or event["supplement_id"] == selected]
    c1, c2, c3 = st.columns(3)
    c1.metric("已服用", sum(event["status"] == "taken" for event in events))
    c2.metric("已略過", sum(event["status"] == "skipped" for event in events))
    c3.metric("已撤銷", sum(event["status"] == "undone" for event in events))
    st.caption("這些是記錄筆數，不是服用達成率。未記錄不等於略過；舊版／CLI 記錄不自動歸入任何時段。")
    rows = [{"記錄時間": event["recorded_at"], "名稱": event["name"],
             "排程": "舊版／CLI" if event["slot"] == "legacy" else "需要時" if event["slot"] == "as_needed" else event["slot"],
             "用量": q(event["dose"]), "單位": event["unit"], "狀態": STATUS[event["status"]], "備註": event["note"]} for event in events]
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.download_button("↓ 匯出這段紀錄 CSV", csv_bytes(rows), f"supplemind-history-{date.today()}.csv", "text/csv")
        with st.expander("更正記錄（撤銷並同步庫存）"):
            for event in events:
                if event["status"] != "undone" and event["slot"] != "legacy":
                    if st.button(f"撤銷 {event['recorded_at']} · {event['name']} · {STATUS[event['status']]}", key=f"history_undo_{event['id']}"):
                        act(manager.undo, "已撤銷記錄並同步庫存", event["id"])
    else:
        st.info("這段期間尚無記錄。從「今天」開始留下第一筆吧。")
    with st.expander("匯出保健品清單"):
        rows = [{"名稱": item.name, "單位": item.unit, "庫存": item.stock_quantity,
                 "到期日": item.expiry_date.isoformat(), "排程": schedule_label(manager.profile(item.id)),
                 "含量": manager.profile(item.id)["strength"], "備註": manager.profile(item.id)["notes"],
                 "已封存": manager.profile(item.id)["archived"]} for item in items]
        st.download_button("↓ 匯出完整清單 CSV", csv_bytes(rows), "supplemind-supplements.csv", "text/csv", disabled=not rows)
        st.caption("匯出包含個人健康資訊，請只分享給你信任的對象。")


def care_view(manager, cloud=False):
    st.markdown("### 多一點留意，少一點擔心")
    active_ids = {item.id for item in manager.items()}
    active_names = {item.name for item in manager.items()}
    alerts = [alert for alert in manager.check_alerts() if alert.name in active_names]
    if not alerts:
        st.success("目前沒有低庫存或 30 天內效期提醒。")
    for alert in alerts:
        (st.error if alert.level == "expired" else st.warning)(f"{alert.name} · {alert.message}")
    st.markdown("#### 個人搭配備註")
    st.info("記下保健品之間的個人備註，方便日後查看。內容由你自行填寫，App 不會自動產生搭配建議。")
    for conflict in manager.list_conflicts():
        if conflict.supplement_id_a in active_ids and conflict.supplement_id_b in active_ids:
            with st.container(border=True):
                st.write(f"{conflict.name_a} ＋ {conflict.name_b}")
                st.caption("個人自訂內容 · 非 App 建議")
                st.caption(conflict.note or "尚未填寫個人備註。")
    items = manager.items()
    if len(items) >= 2:
        with st.expander("新增搭配備註"):
            names = {item.id: item.name for item in items}
            with st.form("conflict"):
                a = st.selectbox("第一項", list(names), format_func=names.get)
                b = st.selectbox("第二項", list(names), index=1, format_func=names.get)
                note = st.text_area("個人備註", placeholder="例如：早餐後一起記錄，或其他想留存的事項。")
                if st.form_submit_button("儲存備註"):
                    act(manager.add_conflict, "搭配備註已儲存", a, b, note)
    if cloud:
        st.markdown("#### 私人雲端記錄")
        st.caption("資料儲存在你的 Supabase PostgreSQL，透過 Streamlit 提供介面，並非端對端加密。只有指定帳號可登入此 App；平台與資料庫管理者仍有管理權限。免費服務可能休眠或暫停，請定期匯出備份。")
        st.caption("iPhone：在 Safari 開啟本站，選擇「分享 → 加入主畫面」。需要網路連線，沒有離線模式或背景通知。")
    else:
        st.markdown("#### 你的資料，留在本機")
        st.caption("SQLite 儲存於本機，不會上傳雲端。資料庫未加密，請保護電腦與備份。此版本限本機單人使用，沒有登入驗證；請勿直接公開到網際網路。")
    st.caption("SuppleMind 專注於保健品、服用記錄與庫存管理，不提供服用或搭配建議。")


def main(cloud=False):
    st.set_page_config(page_title="SuppleMind · 保健日常", page_icon="🌿", layout="centered")
    html(f"<style>{(ROOT / 'supplemind' / 'style.css').read_text()}</style>")
    db_path = os.environ.get("SUPPLEMIND_DB", str(ROOT / "health_tracker.db"))
    html('<div class="brand"><span class="brand-symbol">✚</span><span>SuppleMind</span><span class="brand-tag">保健日常</span></div>')
    html('<div class="page-heading"><h1>好好照顧，每一天。</h1><p>你的保健品、你的節奏。一個地方，安心記錄。</p></div>')
    if cloud:
        from .cloud import AuthenticationError, CloudConfig, login_panel, open_cloud_tracker
        try:
            config = CloudConfig.load(st.secrets)
        except AuthenticationError as exc:
            st.error(str(exc))
            st.stop()
        login_panel(config)
        factory = lambda: open_cloud_tracker(config)
    else:
        factory = lambda: Tracker(db_path)
    if "notice" in st.session_state:
        st.toast(st.session_state.pop("notice"), icon="✅")
    tab_today, tab_items, tab_add, tab_history, tab_care = st.tabs(["今天", "我的保健品", "新增", "紀錄", "提醒與備註"])
    try:
        manager = factory()
    except SupplementError as exc:
        st.error(str(exc))
        st.stop()
    with manager:
        with tab_today:
            @st.fragment(run_every="60s")
            def live_today():
                try:
                    with factory() as current:
                        today_view(current, cloud=cloud)
                except SupplementError as exc:
                    st.error(str(exc))
            live_today()
        with tab_items:
            collection_view(manager)
        with tab_add:
            st.markdown("### 加入一份新的日常")
            item_form(manager)
        with tab_history:
            history_view(manager)
        with tab_care:
            care_view(manager, cloud=cloud)
    html('<div class="footer">SUPPLEMIND · 為自己，留一點照顧的時間<br><span>保健品記錄 · 日常排程 · 庫存管理</span></div>')