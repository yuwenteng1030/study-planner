# 截圖

2026-10-22 擷取，Streamlit 1280×1000 視窗（輸出 800×625）。

| 檔案 | 內容 | 在履歷／README 裡要講的一句話 |
|---|---|---|
| `01-home-排程結果與主題優先度.jpg` | 排程結果、時間利用率 99%、主題優先度表（優先度／分數／排進計畫／為什麼） | 排序規則是公開的，每一列都說得出分數怎麼來的 |
| `02-今日任務-打勾與進度.jpg` | 今日清單、打勾、實際分鐘數欄位、兩條進度條 | 打勾即寫入，進度同一輪更新；實際時間從第一天就開始收，用來校正預估 |

**資料來源**：用一份**乾淨的展示資料庫**產生（`scratchpad/demo.db`），不是 `study.db`。
條件是 14 天 × 每天 3 小時（＝《全職求職時程》每日作息表的演算法時段），
目標選「程式上機測驗（Python 基礎與資料結構）」，排出 92 題 / 2495 分鐘，
30 題沒排進去並附原因。

重拍方式（`.claude/launch.json` 的 `study-planner-demo` 設定，走 8502 埠，
不會碰到 `study.db`）：

```
DATABASE_URL=sqlite:////<path>/demo.db python scripts/import_data.py
DATABASE_URL=sqlite:////<path>/demo.db streamlit run app/Home.py --server.port 8502
```
