# SuppleMind 手機網頁版

網站：https://DoraC7.github.io/supplement-tracker/

使用 Safari 開啟，可選「分享 → 加入主畫面」。不需要 Streamlit、Supabase 或登入帳號。

## 功能
- 每日／指定星期／需要時排程、起迄日期、各時段用量。
- 已服用／略過、撤銷回補庫存、歷史快照。
- 編輯、同效期補貨、封存／恢復、低庫存及效期提醒。
- 完整 JSON 備份與還原；還原經驗證及確認後才取代本機資料。
- IndexedDB 交易避免同一瀏覽器多分頁重複扣庫存。

## 資料與限制
資料只存在此網站來源的瀏覽器 IndexedDB。不自動同步、無背景通知、無離線載入保證。清除瀏覽器資料、私密瀏覽或裝置儲存回收可能遺失資料。Safari 與主畫面版可能有不同儲存空間，請固定同一入口使用並備份。不同裝置透過 JSON 手動移轉；沒有合併功能。

備份未加密，可自行存入 Proton Drive。請以手機裝置鎖保護資料。GitHub 僅提供靜態檔案，不接收保健品紀錄；仍可能保留一般網站連線日誌。

現有 Python 程式與 health_tracker.db 不在部署內容中，也不會自動匯入網頁版。本次只發布 web/。

## 開發與部署
Node.js 22+ 執行 `node --test web/core.test.mjs`。網站為純 HTML/CSS/ES modules，不需 npm 套件。GitHub Settings → Pages 選 GitHub Actions；main 上 web/ 更新會執行測試並部署。