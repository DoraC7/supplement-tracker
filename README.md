# SuppleMind

專注於保健品記錄、日常排程與庫存管理的本機 App，搭配原有 CLI，使用 SQLite 儲存資料。網頁需 Python 3.10 以上及 Streamlit 1.50 以上；目前以 Python 3.14 驗證。

## 私人雲端版（iPhone Safari）

新增 `cloud_app.py` 作為需登入的雲端入口，使用 Supabase Auth 指定使用者白名單及私人 PostgreSQL schema。雲端固定台北時間；缺少 Secrets 或驗證失敗時停止存取，不退回 SQLite。

完整步驟見 [免費雲端部署說明](deployment/README.md)。尚需完成平台 Secrets、資料表建立及真實上線驗證；本機資料不自動上傳。**不可將無登入的 `app.py` 直接公開。**

## 專案結構

```
supplemind/
  __init__.py     # 對外公開 API（向下相容匯出）
  __main__.py     # `python3 -m supplemind` 進入點
  errors.py       # 例外類別
  models.py       # Supplement / IntakeLog / Alert 資料模型
  repository.py   # SupplementManager：SQLite 資料存取層
  services.py     # 商業邏輯：消耗速率預測、今日彙整（不直接碰資料庫）
  tracker.py      # 排程、逐劑事件、封存、原子化庫存與撤銷
  ui.py           # 五分頁 Streamlit 介面
  style.css       # 響應式淺色卡片樣式（原創圖示）
  formatting.py   # 表格與文字輸出格式化
  cli.py          # argparse 參數解析與指令派送
app.py            # Streamlit 啟動入口
tests/
  test_supplemind.py
  test_services.py
  test_tracker.py
  test_ui.py
```

## 網頁 Dashboard（Streamlit）

以每日記錄為中心：按每個時段分別記錄服用或略過，不再把一天內的多劑合併成一次勾選。

安裝與啟動：

```bash
python3.14 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/streamlit run app.py
```

啟動後開啟 `http://127.0.0.1:8501`，包含五個分頁：

- **今天**：依時間排列的逐劑卡片、已服用／略過、記錄進度、撤銷回補庫存、需要時服用、低庫存及效期提醒。頁面開啟時每 60 秒更新。
- **我的保健品**：搜尋、含量與外觀、編輯排程／備註、同效期補貨、封存／恢復（保留原排程）。
- **新增**：名稱、類型、含量、識別色、單位、每日多時段／指定星期／需要時、起迄日期、各時段用量及庫存。
- **紀錄**：日期區間／品項篩選、狀態統計、歷史撤銷、UTF-8 CSV 清單與記錄匯出。已撤銷事件仍保留供查核。
- **提醒與備註**：庫存／效期、個人搭配備註、本機隱私與功能限制。

### 資料相容與操作規則

- 首次開啟只新增 `TrackerProfiles`、`DoseEvents` 及索引，不刪除或重寫原有品項與服用記錄。更新前仍建議備份資料庫。
- 舊品項保持「尚未設定」，不憑空設定服用時間或劑量；請在「我的保健品」編輯。
- 服用記錄與扣庫存在單一交易完成；重複記錄同一排程會被拒絕。略過不扣庫存，撤銷服用會回補一次並保留撤銷事件。
- 網頁阻擋過期及庫存不足的服用記錄。CLI 維持舊行為，不具網頁排程、封存與過期阻擋語意；日常記錄請統一使用網頁，避免重複記錄。
- 編輯排程立即影響目前待辦，已記錄的名稱、劑量、時間快照不會改寫。歷史頁以事件為準，不用新排程倒推舊達成率。不支援補登過去日期。
- 不同效期批次請另建品項；原品項庫存單位不能直接更換。補貨數量以增加方式更新，不覆寫舊畫面庫存。
- 網頁資料庫固定在專案根目錄 `health_tracker.db`；可用 `SUPPLEMIND_DB` 指定隔離資料庫。CLI 預設路徑仍相對於工作目錄。

### 功能範圍

SuppleMind 是純粹的保健品記錄工具，使用自行設計的介面與圖示。

這是本機網頁 App，目前提供頁內提醒與 CSV 匯出，尚無雲端同步、相機辨識、背景推播、鎖定畫面通知、週期性排程或 PDF 匯出。

所有時間使用伺服器本機時區。超過時間的提示只表示尚未記錄，請依實際情況選擇已服用或略過。個人搭配備註由使用者自行填寫，App 不提供服用或搭配建議。

資料庫未加密，無登入或多人權限控制；預設只監聽 `127.0.0.1`，請勿直接公開服務。Streamlit 使用統計已關閉，請妥善保護本機、備份及匯出資料。

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
./.venv/bin/python3.14 -m unittest discover -s tests -p 'test_*.py'
```

資料層與 Streamlit 互動測試使用記憶體或臨時 SQLite，不寫入真實使用者資料。
