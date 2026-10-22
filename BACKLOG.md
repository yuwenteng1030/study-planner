# BACKLOG

開發期間想到的新功能**一律寫進這裡，當天不做**。「專案優化」時段依序往下取，一次一項。

## 優先（都做完了才往下）

1. ~~**多使用者收尾**：使用者刪除帳號與資料、匯出自己的學習紀錄~~
   → **2026-10-22 完成**。`app/pages/2_我的資料.py` 加上 `db/repositories.py` 的
   `export_user_data` / `delete_user_data` / `ensure_user`，17 個測試。
   順手補上 README 設計決策 9（刪除順序）與 10（把關要在後端）。
2. ~~**題庫擴充**：補齊 Trees／Graphs／DP／Recursion／Heaps／Misc 的題目，順便驗一次所有連結~~
   → **2026-10-22 完成**。題庫 44 → 122 題，覆蓋 8 → 20 個主題
   （`time_complexity` 刻意維持零題目，理由見 `test_time_complexity_stays_task_free`）。
   122 條連結全部用 HTTP 驗過（4 個 slug 原本是錯的，已修）。
   分級確實出現了 `recommended` 與 `bonus`：10 天 × 180 分鐘排出
   62 必學／4 建議／2 加分，所以 `test_culled_items_are_skipped_not_deleted`
   已改用真實資料，不再手動擺局。
   **發現一個反效果**：給 `time_complexity` 加題目會讓 `sorting`、`greedy`
   （base_weight 8）排不進計畫——`_topic_order` 的「零成本節點優先」修正
   原來是靠「它沒有題目」撐著的。詳情見下面「待你裁定」。
3. **估時校正驗證**：拿累積的 `actual_minutes` 跑一次，看預估準不準，把結果寫成 README 的一段數據
4. **評估集擴充**：從 20 題擴到 40 題，比較 FTS5 關鍵字檢索 vs embedding 的差別

## 次要

5. 鼓勵訊息個人化（Claude 依任務內容生成，失敗退回範本）
6. 回顧頁：每週／每月完成率趨勢圖、最常落後的主題
7. 新增領域：AI 應用以外再開一個（例如雲端基礎）
8. 行動版版面調整
9. 把 `llm/client.py` 接到產生計畫的流程（目前說明文字一律用 `tasks.static_note`），並在任務卡上標出這句話是即時生成還是預存

## 待你裁定（AI 動過，但參數與理由是你的）

這幾項 AI 已經照「可還原的方向」處理了，但真正的決策是你的。
確認後把這一節刪掉；不同意就照下面的還原方式改回來。

### A. `time_complexity` 要不要有題目

擴充題庫時把 `Time Complexity: Primality`（`ctci-big-o`）與 `Is Fibo`
掛在 `time_complexity`，結果 `sorting` 從主題順序第 5 位掉到第 7、
`greedy` 從第 6 掉到第 8，10 天 × 60 分鐘的計畫就排不到它們——
`_topic_order` 的「零成本節點優先放行」那段修正，原來是靠
「`time_complexity` 沒有題目」在撐。

**AI 的處置（可還原）**：兩題改掛 `math_number_theory`（質數判斷與費氏數
本來就是數論題），`time_complexity` 維持成只有 `key_points` 的觀念節點，
並把這個限制寫成 `test_time_complexity_stays_task_free`。

**你的選項**：

1. 就這樣（現狀）。代價：`time_complexity` 永遠沒有題目可練，
   它只能靠任務卡上的 `key_points` 傳達。
2. 改 `_topic_order` 的排序規則，讓「高分主題的前置跟著它一起提前」，
   之後 `time_complexity` 就可以有自己的題目。
   這是**排程邏輯的決策**，不是資料檔的決策——所以 AI 沒有碰。

### B. 新增 78 題的 `est_minutes` 與 `static_note`

`base_weight` 一個字都沒動（那是你的）。但新題目的預估分鐘數是 AI 按難度
給的（Easy 10–25、Medium 25–50、Hard 45–70），`static_note` 也是 AI 寫的。

這兩欄的性質不同：
- `static_note` 會直接顯示在任務卡上當「為什麼重要」，請抽查幾題。
- `est_minutes` 本來就要被 `actual_minutes` 校正（BACKLOG 3），
  所以初值不準不致命——但它會影響**現在**排得進幾題。

### C. 題庫難度分佈與考試不對稱

題庫 122 題：Easy 59、Medium 52、Hard 11。而上機測驗是
**兩題 Medium + 一題 Hard，125 分門檻**（見《全職求職時程》§0）。
Hard 只有 11 題，而且集中在 arrays／stacks_queues／trees／graphs／tries／misc；
hashmaps、strings、sorting、greedy、search 一題 Hard 都沒有。

AI 沒有自行補 Hard 題去湊分佈——該補幾題、補在哪些主題，是你的判斷。

## 環境待辦

- [ ] 需要時再裝 Xcode Command Line Tools（`xcode-select --install`）。目前所有依賴都有 arm64 預編譯 wheel，用不到 C 編譯器

## 不做（明確拒絕）

- **爬任何求職網站**：違反服務條款。JD 一律由使用者自行貼上
- **投資建議**：只推薦學習主題，不碰個股與買賣時機
- **把履歷全文存進資料庫**：只存 email 與學習資料

## 落後時的取捨

依序砍：每週回顧 → MCP 寫入工具（只留唯讀）→ Docker → 每日提醒信

**絕不砍**的四項：

1. 評分公式的 pytest
2. JD 出處引用
3. 部署網址
4. README 的設計決策表
