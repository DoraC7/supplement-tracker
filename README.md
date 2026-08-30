# SuppleMind

可直接使用的保健品追蹤 CLI，使用 SQLite 儲存資料，不需要額外套件。

## 專案結構

```
supplemind/
  __init__.py     # 對外公開 API（向下相容匯出）
  __main__.py     # `python3 -m supplemind` 進入點
  errors.py       # 例外類別
  models.py       # Supplement / IntakeLog / Alert 資料模型
  repository.py   # SupplementManager：SQLite 資料存取層
  services.py     # 商業邏輯：消耗速率預測、今日彙整（不直接碰資料庫）
  formatting.py   # 表格與文字輸出格式化
  cli.py          # argparse 參數解析與指令派送
app.py            # Streamlit 極簡網頁 Dashboard（重用 supplemind package）
tests/
  test_supplemind.py
  test_services.py
```

## 網頁 Dashboard（Streamlit）

參考「一天一動作」App 的極簡風格：打開先看「今天該做什麼」，一鍵記錄，不需要研究一堆設定。

安裝與啟動：

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

啟動後瀏覽器會自動開啟 `http://localhost:8501`，包含四個分頁：

- **今天**：核心畫面。庫存/效期提醒 + 今天待辦（一鍵記錄服用）+ 已完成清單 + 剩餘天數進度條。
- **保健品**：清單 + 補貨。
- **新增**：單一表單新增保健品。
- **紀錄**：服用歷史查詢 + 消耗速度預估。

## 功能

- 新增保健品資料
- 記錄服用時間與劑量
- 自動扣庫存，避免扣到負數
- 補貨
- 檢查低庫存與即將到期提醒
- 查詢指定天數內的服用紀錄
- 依過去服用紀錄預估每日消耗速度與剩餘天數（`forecast`）
- 一次看完今日提醒、消耗預估、今日服用狀況（`today`）

## 執行方式

```bash
python3 -m supplemind --help
```

預設會在目前目錄建立 `health_tracker.db`。

## 指令範例

新增保健品：

```bash
python3 -m supplemind add \
  --name "維他命 B群" \
  --unit "粒" \
  --stock 15 \
  --warning 10 \
  --expiry 2027-01-01
```

列出所有保健品：

```bash
python3 -m supplemind list
```

記錄服用：

```bash
python3 -m supplemind take --id 1 --dosage 1
```

補貨：

```bash
python3 -m supplemind restock --id 1 --amount 30
```

查看提醒：

```bash
python3 -m supplemind alerts --days 30
```

查看最近 14 天紀錄：

```bash
python3 -m supplemind history --days 14
```

預估消耗速度與剩餘天數（依過去 30 天服用紀錄）：

```bash
python3 -m supplemind forecast --lookback-days 30
```

一次看今天該注意的所有事情（提醒 + 消耗預估 + 今日服用狀況）：

```bash
python3 -m supplemind today
```

## 測試

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```
