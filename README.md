# study-planner

想學一樣東西，但不知道「現在最該學什麼」——這個網頁回答這件事。

使用者填下想學的主題、每天可投入幾小時、希望幾天內完成，再補一句自由文字目標（例如「我想學 Python，目標是台積 IT／IMC 的上機測試」）。網站排出該做哪些項目、每天提醒要完成什麼、並說明**每一項為什麼重要**。

> 開發中。目前進度：v0.1 開發前置（環境與題庫）。

## 跟「直接問 ChatGPT」有什麼不同

| | 聊天機器人 | study-planner |
|---|---|---|
| 排序依據 | 每次回答都不一樣 | 固定的評分公式，可重現、有 pytest |
| 根據 | 模型印象 | 使用者貼上的 JD，附出處引用 |
| 時間估計 | 猜的 | 從你實際花的時間反推校正 |
| 落後了 | 要自己再問一次 | 自動重排，主動提醒 |

## 評分公式

```
score(topic) = base_weight × jd_factor × gap_factor × goal_factor
```

| 因子 | 範圍 | 說明 |
|---|---|---|
| `base_weight` | 1–10 | 主題本身的重要性（Hash Map 10、兩指標 9、…） |
| `jd_factor` | 1–2 | `1 + (提到該主題的 JD 份數 ÷ 總份數)`；**沒有任何 JD 時固定為 1** |
| `gap_factor` | 0.3 / 1.0 / 1.5 | 已熟／普通／沒學過 |
| `goal_factor` | ×1.3 | 命中使用者自由文字目標時加權 |

排序後依百分位切三層：前 40% 必學、次 35% 建議、其餘加分。評分結果用 `data/golden_cases.yaml` 的黃金案例做迴歸測試。

## 架構

```
Streamlit (app/)  →  FastAPI (api/)  →  core/  →  db/repositories/  →  SQLite
                                         ↓
                                    llm/client.py  →  Claude API
```

分層原則：router 只處理 HTTP、service 放邏輯、repository 碰資料庫。**換資料庫只改 repository 這一層。**

## 多使用者

最終目標是開放多使用者，但第一版**刻意不實作登入**。做法是：

- 所有使用者資料表都帶 `user_id`
- 所有查詢都經過 repository 層
- API 一律從 `api/deps.py` 的 `get_current_user()` 取得身分——v0.1～v0.3 這支函式固定回傳 `user_id=1`

開放登入時只需要改這一支函式，加一個登入頁。

## 技術

Python 3.14、Streamlit、FastAPI、SQLAlchemy、SQLite（之後 PostgreSQL）、Claude API、pytest、Docker、GitHub Actions

## 開發

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 資料來源聲明

本專案所有資料均為**自行整理的公開題庫資訊與自行生成的模擬資料**，不包含任何前雇主的真實資料或程式碼。

JD 一律由使用者自行貼上，不爬取任何求職網站。
