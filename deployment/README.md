# 免費私人雲端部署

## 目前狀態

程式支援雲端模式，但新增檔案不代表已部署。須完成下列帳號、資料表、Secrets 設定與上線驗證。只部署 `cloud_app.py`，**不要把沒有登入的 `app.py` 公開**。

## 1. Supabase 專案與私人資料表

- 已建立專案 `nsswoufxjdwzbzdjazqy`（首爾區域），使用 Free 組織。
- 在 SQL Editor 執行 `deployment/schema.sql` **一次**。只建立專用 `supplemind` schema，不匯入本機資料；重複執行會回滾，不刪除既有資料。
- 此 schema 不得加入 Data API 的 Exposed schemas；`anon`／`authenticated` 不可取得 schema 或表格權限。
- 腳本會調整執行者的預設 ACL，建議在這個專用新專案執行；不要不經檢查套用到共用正式資料庫。
- 執行者應是 migration owner；App 使用獨立、無建表／管理權限的 LOGIN role，不能用 `postgres` 主帳號作為長期 App 憑證。

在 SQL Editor **自行填入新產生的資料庫 App 密碼**後執行以下設定。請勿將真實密碼存進檔案、Git、對話或截圖。可使用 Proton Pass 產生並保存。

```sql
CREATE ROLE supplemind_login LOGIN NOINHERIT NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD 'REPLACE_PRIVATELY';
GRANT supplemind_app TO supplemind_login;
```

## 2. 建立 App 唯一登入帳號

平台的 GitHub 登入不等於 App 登入：

1. Supabase → Authentication → Users → Add user → Create new user。
2. 自行輸入信箱與獨立登入密碼，確認是自己的信箱；設為已確認。
3. 在 Authentication 設定關閉「Allow new users to sign up」。
4. 複製這個使用者的 UUID 至 Secrets 的 `owner_user_id`。不是組織 ID、GitHub ID，也不是專案 ID。

App 每次存取前向 Supabase 驗證 access token、使用者 UUID 與信箱確認狀態；非指定帳號不會連線資料庫。登入過期需重新登入，不把密碼或 refresh token 寫入資料庫。

## 3. Streamlit 部署與 Secrets

1. 把已審查的程式推送 GitHub（不要提交 `.db`、`.env`、`.streamlit/secrets.toml`）。
2. Streamlit → Create app → GitHub repository `DoraC7/supplement-tracker`。
3. 選含本次修改的分支，Main file path：`cloud_app.py`；Python 選 3.12 或平台支援的較新版本。
4. Advanced settings → Secrets：參考 `.streamlit/secrets.example.toml`，直接填入平台，不貼到對話中。
5. `publishable_key` 使用 Supabase publishable／anon key，**不是 service_role／secret key**。
6. `database_url` 使用 Connect → Session pooler（IPv4，port 5432）所顯示的實際主機，帳號改為 `supplemind_login.PROJECT_REF`，密碼改為步驟 1 的 App 資料庫密碼。密碼中的特殊字元需要 URL encoding。
7. App 持有資料庫專用帳號，只在伺服器使用；不傳給 iPhone。儲存用資料庫帳號與 App 的使用者登入密碼相互獨立。
8. 可另外在 Streamlit Sharing 將 App 設為私人；不論分享設定如何，程式仍要求指定 Supabase 帳號登入。

目前預覽主機僅監聽 `127.0.0.1`；Community Cloud 應使用平台管理的連線設定。若健康檢查無法到達，移除本機 `server.address` 或由平台覆寫成 `0.0.0.0`，不可因此改用無登入入口。

## 4. 上線驗證（必須完成）

- 未登入／錯誤帳號看不到清單、庫存、備註或下載檔。
- 正確帳號新增一項測試品，記錄／略過／撤銷後庫存符合預期。
- 重新啟動 App 後資料仍在；確認資料實際存放 Supabase，不是本機 SQLite。
- 登出與權限取消後無法繼續操作；已下載的 CSV 不會被撤回，請妥善保管。
- 用 iPhone Safari 的行動網路開啟 HTTPS 網址測試，非同 Wi-Fi 本機網址。
- 台北時間跨日判定正確（雲端 process 固定 `Asia/Taipei`），不同裝置顯示一致。

只通過離線測試不等於雲端驗證完成。`tests/test_postgres.py` 的真實連線測試僅能使用可丟棄的測試資料庫，不使用個人正式資料。

## 5. 手機與資料保存

Safari → 分享 → 加入主畫面。這是需要網路的網頁 App，不支援離線、背景通知或原生 iOS 安裝。

Supabase 免費方案可能因閒置暫停，Streamlit 也可能休眠。不保證永久免費與 24 小時不中斷。定期匯出清單與記錄 CSV；CSV 不等於完整資料庫備份。

本機舊資料仍保留，**不會自動上傳**。資料移轉需先備份、確認要上傳的內容，再做一次性轉入與數量核對。

## 安全邊界

這是單一使用者 App，不是多租戶。Auth 白名單在伺服器執行；資料庫以私有 schema ACL 隔離，不宣稱 RLS 多使用者隔離或端對端加密。資料庫管理者與平台管理者有管理權限。TLS `sslmode=require` 加密連線但不驗證伺服器主體；部署時應使用平台可信連線資訊，後續可配置受信任 CA 與 `verify-full`。