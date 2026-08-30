"""SuppleMind — 極簡保健品追蹤 Dashboard (Streamlit)。

設計原則（參考 Soliday 的極簡風格）：
  - 一次只做一件事：打開就先看到「今天該吃什麼」，而不是一堆設定或表格。
  - 大留白、少色彩：只用黑/灰當主色，警示才用顏色，避免視覺噪音。
  - 一鍵完成：記錄服用只要按一個按鈕，不需要跳出很多欄位。
  - 不重寫邏輯：所有商業邏輯都直接重用 `supplemind` package
    （`repository.py` / `services.py`），這裡只負責畫面呈現。
"""

from __future__ import annotations

import streamlit as st

from supplemind.formatting import format_quantity
from supplemind.repository import DEFAULT_DB_PATH, SupplementManager
from supplemind.services import DEFAULT_LOOKBACK_DAYS, forecast_all, get_daily_briefing

st.set_page_config(
    page_title="SuppleMind",
    page_icon="💊",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# 極簡風格 CSS：拿掉 Streamlit 預設的多餘元素，加大留白與字級對比。
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        #MainMenu, footer, header {visibility: hidden;}
        .block-container {padding-top: 2.5rem; padding-bottom: 3rem; max-width: 640px;}
        h1 {font-weight: 700; letter-spacing: -0.02em;}
        .supple-card {
            border: 1px solid #ECECEC;
            border-radius: 14px;
            padding: 1rem 1.2rem;
            margin-bottom: 0.6rem;
            background: #FFFFFF;
        }
        .supple-card-done {
            border: 1px solid #ECECEC;
            border-radius: 14px;
            padding: 0.7rem 1.2rem;
            margin-bottom: 0.4rem;
            background: #FAFAF9;
            color: #9A9A93;
        }
        .supple-name {font-size: 1.05rem; font-weight: 600; margin: 0;}
        .supple-meta {font-size: 0.85rem; color: #8A8A83; margin: 0;}
        .supple-empty {text-align: center; color: #9A9A93; padding: 2rem 0;}
        div[data-testid="stMetricValue"] {font-size: 1.4rem;}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource(show_spinner=False)
def get_manager(db_path: str) -> SupplementManager:
    return SupplementManager(db_path)


def refresh() -> None:
    st.rerun()


manager = get_manager(str(DEFAULT_DB_PATH))

st.markdown("# SuppleMind")
st.caption("一天看一次就好。")

tab_today, tab_list, tab_add, tab_history = st.tabs(["今天", "保健品", "新增", "紀錄"])

# ---------------------------------------------------------------------------
# 今天：核心畫面，仿照 Soliday「打開、做完、走人」的單一動線。
# ---------------------------------------------------------------------------
with tab_today:
    briefing = get_daily_briefing(manager)

    if briefing.alerts:
        for alert in briefing.alerts:
            icon = "🔴" if alert.level == "expired" else "⚠️"
            st.warning(f"{icon} **{alert.name}** — {alert.message}")

    st.markdown("### 待辦")
    if not briefing.pending_today:
        st.markdown(
            '<div class="supple-empty">今天都完成了 ✅</div>',
            unsafe_allow_html=True,
        )
    else:
        for supplement in briefing.pending_today:
            with st.container():
                st.markdown('<div class="supple-card">', unsafe_allow_html=True)
                col_info, col_dose, col_action = st.columns([3, 2, 2])
                with col_info:
                    st.markdown(f'<p class="supple-name">{supplement.name}</p>', unsafe_allow_html=True)
                    st.markdown(
                        f'<p class="supple-meta">庫存 {format_quantity(supplement.stock_quantity)}'
                        f'{supplement.unit}</p>',
                        unsafe_allow_html=True,
                    )
                with col_dose:
                    dosage = st.number_input(
                        "劑量",
                        min_value=0.0,
                        value=1.0,
                        step=1.0,
                        key=f"dose_{supplement.id}",
                        label_visibility="collapsed",
                    )
                with col_action:
                    if st.button("完成", key=f"take_{supplement.id}", width="stretch"):
                        try:
                            manager.take_supplement(supplement.id, dosage=dosage)
                            st.toast(f"已記錄 {supplement.name}", icon="✅")
                            refresh()
                        except Exception as exc:  # noqa: BLE001 — surface any domain error to the user
                            st.error(str(exc))
                st.markdown("</div>", unsafe_allow_html=True)

    if briefing.taken_today:
        st.markdown("### 已完成")
        for log in briefing.taken_today:
            st.markdown(
                f'<div class="supple-card-done">✅ {log.name} '
                f'· {format_quantity(log.dosage)}{log.unit} · '
                f'{log.taken_at.strftime("%H:%M")}</div>',
                unsafe_allow_html=True,
            )

    if briefing.forecasts:
        st.markdown("### 剩餘天數")
        for forecast in briefing.forecasts:
            if forecast.days_remaining is None:
                continue
            max_days = 60.0
            ratio = min(1.0, forecast.days_remaining / max_days)
            col_name, col_bar, col_days = st.columns([2, 4, 2])
            with col_name:
                st.write(forecast.name)
            with col_bar:
                st.progress(ratio)
            with col_days:
                st.write(f"{forecast.days_remaining:.0f} 天")

# ---------------------------------------------------------------------------
# 保健品清單：簡單表格 + 補貨。
# ---------------------------------------------------------------------------
with tab_list:
    supplements = manager.list_supplements()
    if not supplements:
        st.markdown('<div class="supple-empty">還沒有任何保健品，先到「新增」加一項。</div>', unsafe_allow_html=True)
    else:
        for supplement in supplements:
            with st.container():
                st.markdown('<div class="supple-card">', unsafe_allow_html=True)
                col_info, col_restock_amount, col_restock_btn = st.columns([3, 2, 2])
                with col_info:
                    st.markdown(f'<p class="supple-name">{supplement.name}</p>', unsafe_allow_html=True)
                    st.markdown(
                        f'<p class="supple-meta">庫存 {format_quantity(supplement.stock_quantity)}'
                        f'{supplement.unit} · 警戒值 {format_quantity(supplement.warning_level)}'
                        f'{supplement.unit} · 到期 {supplement.expiry_date.isoformat()}</p>',
                        unsafe_allow_html=True,
                    )
                with col_restock_amount:
                    restock_amount = st.number_input(
                        "補貨量",
                        min_value=0.0,
                        value=0.0,
                        step=1.0,
                        key=f"restock_amount_{supplement.id}",
                        label_visibility="collapsed",
                    )
                with col_restock_btn:
                    if st.button("補貨", key=f"restock_{supplement.id}", width="stretch"):
                        if restock_amount > 0:
                            manager.restock_supplement(supplement.id, restock_amount)
                            st.toast(f"已補貨 {supplement.name}", icon="📦")
                            refresh()
                        else:
                            st.warning("請輸入大於 0 的補貨量。")
                st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 新增：單一表單，欄位精簡。
# ---------------------------------------------------------------------------
with tab_add:
    with st.form("add_supplement_form", clear_on_submit=True):
        name = st.text_input("名稱")
        col_unit, col_stock = st.columns(2)
        with col_unit:
            unit = st.text_input("單位（例：粒、包）")
        with col_stock:
            stock = st.number_input("目前庫存", min_value=0.0, value=0.0, step=1.0)
        col_warning, col_expiry = st.columns(2)
        with col_warning:
            warning_level = st.number_input("低庫存警戒值", min_value=0.0, value=0.0, step=1.0)
        with col_expiry:
            expiry_date = st.date_input("到期日")

        submitted = st.form_submit_button("新增", width="stretch")
        if submitted:
            try:
                manager.add_supplement(
                    name=name,
                    unit=unit,
                    stock=stock,
                    warning_level=warning_level,
                    expiry_date=expiry_date.isoformat(),
                )
                st.toast(f"已新增 {name}", icon="🆕")
                refresh()
            except Exception as exc:  # noqa: BLE001 — surface any domain error to the user
                st.error(str(exc))

# ---------------------------------------------------------------------------
# 紀錄：服用歷史，簡單篩選天數。
# ---------------------------------------------------------------------------
with tab_history:
    days = st.slider("查看最近幾天", min_value=1, max_value=90, value=14)
    logs = manager.get_calendar(days=days, limit=200)
    if not logs:
        st.markdown('<div class="supple-empty">這段期間沒有服用紀錄。</div>', unsafe_allow_html=True)
    else:
        st.dataframe(
            [
                {
                    "時間": log.taken_at.strftime("%Y-%m-%d %H:%M"),
                    "名稱": log.name,
                    "劑量": f"{format_quantity(log.dosage)}{log.unit}",
                }
                for log in logs
            ],
            hide_index=True,
            width="stretch",
        )

    with st.expander("消耗速度預估"):
        forecasts = forecast_all(manager, lookback_days=DEFAULT_LOOKBACK_DAYS)
        st.dataframe(
            [
                {
                    "名稱": forecast.name,
                    "平均消耗速度": (
                        f"{forecast.daily_rate:.2f}{forecast.unit}/天" if forecast.days_remaining else "資料不足"
                    ),
                    "預估剩餘天數": (
                        f"{forecast.days_remaining:.1f} 天" if forecast.days_remaining else "資料不足"
                    ),
                }
                for forecast in forecasts
            ],
            hide_index=True,
            width="stretch",
        )
