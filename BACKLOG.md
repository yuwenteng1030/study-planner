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
- ~~**學習地圖（唯讀版）**~~（不在原清單，不佔編號，免得其他文件裡的「BACKLOG #n」對不上）
   → **2026-10-22 完成**（使用者指定提前做）。`app/pages/3_學習地圖.py` ＋ `core/game_map.py`，
   區域從 `topics.prereq` 的最長前置鏈算出，進度跨所有計畫計算，14 個測試。
   **刻意沒做**：星等、解鎖、Boss 關。破關門檻是參數（提案決策 5），看過畫面再定。

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

上機測驗是**兩題 Medium + 一題 Hard，125 分門檻**（見《全職求職時程》§0）。

**2026-10-22 更正**：先前寫「hashmaps、strings、sorting、greedy、search 一題 Hard
都沒有」是錯的——清點時讀錯了 CSV 欄位。實際上 greedy（Cutting Boards）與
search（Maximum Subarray Sum）本來就各有 1 題 Hard。

同一天把題庫的難度全部對過 HackerRank 官方 API，有 7 題標錯，已照官方改正：

| 題目 | 原本 | 官方 |
|---|---|---|
| Merge Sort: Counting Inversions | Medium | **Hard** |
| BFS: Shortest Reach in a Graph | Medium | **Hard** |
| Contacts | Hard | Medium |
| Friend Circle Queries | Hard | Medium |
| Sorting: Comparator | Easy | Medium |
| Is Fibo | Easy | Medium |
| Sum vs XOR | Medium | Easy |

並依你的決定補了 3 題 Hard：strings 加 `Determining DNA Health`、greedy 加 `Chief Hopper`、
search 加 `Making Candies`。三題的 `est_minutes`（70／50／65）與 `static_note` 是 AI 給的，
歸在上面 B 一起抽查。

現在題庫 125 題：Easy 58、Medium 53、Hard 14。**改難度標籤的副作用**：
`misc` 原本唯一的 Hard（Friend Circle Queries）其實是 Medium，所以 misc 變成沒有 Hard；
`tries` 從 2 題 Hard 變 1 題。

**10/22 已決定**：hashmaps 維持沒有 Hard——HackerRank 面試準備包的 Hashmaps 章本身就沒有 Hard，
hashmap 的技巧會出現在別的主題的 Hard 裡（例如 Determining DNA Health）。

### D. 學習地圖的三個顯示選擇

唯讀地圖頁（2026-10-22）裡有三個 AI 自己決定的地方，都只影響畫面、不影響排程：

1. **區號＝到起點的最長前置鏈**。取最短的話，有兩個前置的主題（例如 `sorting`）
   可能跟自己的前置擠在同一區。寫成了 `test_zone_uses_the_longest_prereq_chain`。
2. **同區之內照 `base_weight` 由高到低**。同區主題彼此沒有前置關係，順序只是顯示；
   原本照資料庫的 id 字母序，沒有意義。
3. **進度跨所有計畫算**（含已收起來的）。重排一份新計畫不該讓地圖歸零。

同一天改成插畫版（`core/map_art.py`，你選了原創小熊、蜿蜒小路、進度環）之後，又多了幾個 AI 決定的外觀：

4. **四區的主題名與配色**：起點・小木屋、第一區・草原、第二區・森林、第三區・雪山。
5. **由下往上走**，起點在最下面（照你選的示意圖）；代價是一打開頁面先看到的是最後一區。
6. **小熊站的「下一關」＝路線上第一個還有題目沒做的關卡**，跳過觀念關。
   這只是顯示，沒有鎖住任何關卡，排程照舊。
7. **每排最多 4 關**。手機寬度下字會小，所以原本的文字版收進「每一關的題數」展開區。
8. **做完的關卡變金色＋打勾**；全部做完時小熊停在最後一個有題目的關卡。
9. **區名標籤放在小路往上一區那一側的對面**，免得路壓在字上。
10. **第 4 區以後**（目前沒有）借第三區的顏色，名稱叫「第 n 區」。
11. **關卡名超過 13 字拆兩行**，第二行超過 18 字才截斷加「…」（留住「（一維）／（二維）」）。
12. 插畫是一張圖（`st.image`），**滑鼠移上去沒有提示**——`st.html` 會把 SVG 整段濾掉，
    能互動的版本要改用 iframe 元件，等你決定要不要。

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
