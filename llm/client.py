"""Claude API 的最小封裝：產生「為什麼該做這一項」。

**設計上的硬要求：LLM 掛掉時功能不能減。**

所以這個模組只做一件事——有 API key 就生成客製的說明，沒有、額度用完、
或呼叫失敗，就回退到 ``tasks.static_note`` 預存的那句話。使用者看到的差別
只是文字從客製變罐頭，計畫照排、進度照記、網站照用。

這也是為什麼 ``static_note`` 是題庫的必填欄位，而不是「有空再補」。

刻意**不做**的事（都排在 v0.1 之後）：快取、重試、成本統計、串流。
（``explain_many`` 已在建立計畫時呼叫；任務卡用 ``plan_items.reason_source`` 標出來源。）
v0.1 的目標是跑得起來且不會因為外部服務而壞掉，不是跑得省。
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import threading
from dataclasses import dataclass

from core.encouragement import Cheer, clean_generated

#: 用哪個模型。選 Haiku 是因為這個任務是「把已知重點改寫成一句話」，
#: 不需要推理能力，而便宜與快才直接影響使用體驗。
DEFAULT_MODEL = "claude-haiku-5-5"

#: 生成長度上限。說明是要在任務卡上顯示的，長了沒人看。
MAX_TOKENS = 200

_SYSTEM_PROMPT = """你是一位面試準備教練。使用者正在準備以 Python 為主的\
程式上機測驗。

請針對給定的題目，用**繁體中文**寫一句話（40 到 70 個字），說明為什麼這一題\
值得做。要具體指出它訓練到的技巧，不要寫「這題很重要」這種空話，也不要\
洩漏解法細節。只輸出那一句話，不要加標點以外的任何格式。"""


@dataclass(frozen=True)
class Explanation:
    """一句說明，以及它是怎麼來的。

    ``source``（llm／static／none）記錄這句話的來源。目前畫面還沒顯示它，
    v0.2 接上 LLM 時會標出來，讓使用者知道是即時生成還是預存——
    demo 時也才能說清楚「現在這句是罐頭，因為沒設 API key」。
    """

    text: str
    source: str  # "llm" | "static" | "none"

    @property
    def is_generated(self) -> bool:
        return self.source == "llm"


def is_available() -> bool:
    """有沒有 API key。沒有就全程走備援，不會拋錯。"""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def explain_task(
    title: str,
    topic_name: str,
    key_points: list[str],
    static_note: str | None,
    model: str = DEFAULT_MODEL,
    use_llm: bool = True,
) -> Explanation:
    """產生「為什麼該做這一題」。

    任何失敗都回退到 ``static_note``；``static_note`` 也沒有才回傳空字串。
    這個函式**不會拋例外**——呼叫端不需要包 try。
    """
    fallback = Explanation(static_note, "static") if static_note else Explanation("", "none")

    if not use_llm or not is_available():
        return fallback

    try:
        import anthropic

        client = anthropic.Anthropic(timeout=EXPLAIN_TIMEOUT_SECONDS, max_retries=0)
        hints = "、".join(key_points) if key_points else "（無）"
        response = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"題目：{title}\n"
                        f"所屬主題：{topic_name}\n"
                        f"主題重點：{hints}"
                    ),
                }
            ],
        )
        # 任務卡用 st.info 畫 Markdown：拿掉連結與網址、只留第一行
        text = clean_generated(
            "".join(block.text for block in response.content if block.type == "text")
        )
        if not text:
            return fallback
        return Explanation(text, "llm")
    except Exception:
        # 這裡刻意吞掉所有例外。外部服務的任何問題——沒安裝套件、
        # key 失效、額度用完、超時、回傳格式變了——都不該讓使用者
        # 看不到今天的任務。
        return fallback


#: 同時送幾個請求。一份計畫 60 幾題，逐題等會讓「排出計畫」卡上一分鐘；
#: 8 條夠快，又不會一次打爆速率限制。
EXPLAIN_WORKERS = 8

#: 單題等多久。逾時就用預存說明，不重試。
EXPLAIN_TIMEOUT_SECONDS = 10.0


#: 每位使用者每天最多為幾題呼叫 Claude 寫說明。一份計畫約 60 題，所以 150 夠排兩份全新的，
#: 重排同一批題目靠 ``reuse`` 不重複呼叫，不會吃到額度。存在記憶體，重開伺服器歸零。
EXPLAIN_DAILY_QUOTA = 150
_explain_calls: dict[tuple[int, str], int] = {}


def _reserve_explain_quota(user_id: int, wanted: int, quota: int) -> int:
    """預留最多 ``wanted`` 次，回傳實際可用的次數（可能是 0）。"""
    key = (user_id, _dt.date.today().isoformat())
    with _quota_lock:
        granted = max(0, min(wanted, quota - _explain_calls.get(key, 0)))
        _explain_calls[key] = _explain_calls.get(key, 0) + granted
        return granted


def _refund_explain_quota(user_id: int, count: int) -> None:
    key = (user_id, _dt.date.today().isoformat())
    with _quota_lock:
        _explain_calls[key] = max(0, _explain_calls.get(key, 0) - count)


def explain_many(
    tasks: list[dict],
    model: str = DEFAULT_MODEL,
    *,
    user_id: int | None = None,
    quota: int = EXPLAIN_DAILY_QUOTA,
    reuse: dict[int, str] | None = None,
) -> dict[int, Explanation]:
    """一次處理多題。

    ``tasks`` 每筆需要 ``id``／``title``／``topic_name``／``key_points``／
    ``static_note``。用執行緒並行呼叫（不用批次 API）；沒有 API key 時直接回傳
    預存說明，連執行緒都不開。**不要在資料庫交易裡呼叫它**，理由同 ``encourage``。

    省錢的兩道閘：``reuse``（題目 id → 以前生成過的說明）命中就直接用、不呼叫；
    給了 ``user_id`` 時每天最多為 ``quota`` 題呼叫，超過的退回預存說明。
    """
    reuse = reuse or {}
    out: dict[int, Explanation] = {
        t["id"]: Explanation(reuse[t["id"]], "llm") for t in tasks if reuse.get(t["id"])
    }
    todo = [t for t in tasks if t["id"] not in out]
    allowed = len(todo)
    if user_id is not None and is_available() and todo:
        allowed = _reserve_explain_quota(user_id, len(todo), quota)

    def one(item: tuple[int, dict]) -> Explanation:
        index, task = item
        return explain_task(
            title=task["title"],
            topic_name=task.get("topic_name", ""),
            key_points=task.get("key_points") or [],
            static_note=task.get("static_note"),
            model=model,
            use_llm=index < allowed,
        )

    indexed = list(enumerate(todo))
    if not is_available() or allowed <= 1:
        results = [one(i) for i in indexed]
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=EXPLAIN_WORKERS) as pool:
            results = list(pool.map(one, indexed))
    out.update({task["id"]: r for task, r in zip(todo, results)})
    if user_id is not None and allowed:
        # 失敗的呼叫（key 失效、429、逾時）不該吃掉當天額度：只收成功生成的
        succeeded = sum(1 for r in results[:allowed] if r.is_generated)
        _refund_explain_quota(user_id, allowed - succeeded)
    return out


# ---------------------------------------------------------------------------
# 鼓勵訊息
# ---------------------------------------------------------------------------

#: 鼓勵訊息用的模型。跟上面 ``DEFAULT_MODEL``（Haiku，計畫說明，一次 60 幾題）分開：
#: 鼓勵一次一句、有每日額度，用較強的模型。兩個都是成本決定，見 BACKLOG G 與 I。
ENCOURAGE_MODEL = "claude-opus-5-5"

#: 這個模型的思考一定開著，``max_tokens`` 要含思考的份量；
#: effort 設 low，只是寫一句話，不需要想很久。
ENCOURAGE_MAX_TOKENS = 1024

#: 拒答時讓伺服器改用其他模型重跑（照拒答類別自動挑，不用自己維護模型清單）。
#: 鼓勵訊息幾乎不可能觸發，但開著不花錢；真的被拒答也只是退回範本。
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

_ENCOURAGE_SYSTEM = """你是一位溫暖但不浮誇的程式面試教練。使用者剛在 HackerRank \
做完一題、按下完成。請用**繁體中文**寫一句鼓勵（30 到 60 個字）：要具體提到這一題\
或它的主題練到了什麼，可以呼應今天的進度；不要用「太棒了！」「你是最棒的」這種空話，\
不要給解法，不要加表情符號以外的任何格式。只輸出那一句話。"""

#: 每位使用者每天呼叫幾次的計數：``(user_id, 日期) → 次數``。
#: 存在記憶體裡，重開伺服器會歸零——v0.1 沒有 ``api_usage`` 表（排在 v0.1.5），
#: 這只是擋住「一直打勾取消再打勾」把額度燒光的最低防線。
_calls: dict[tuple[int, str], int] = {}
#: 今天已經用 Claude 鼓勵過的題目：``(user_id, 日期, 題名)``。同一題取消再打勾不再呼叫。
_cheered: set[tuple[int, str, str]] = set()
#: Streamlit 每個分頁一條執行緒，計數的讀改寫要上鎖。
_quota_lock = threading.Lock()

#: 鼓勵訊息等不了太久：使用者正看著打勾的那一刻。逾時就用範本，不重試。
ENCOURAGE_TIMEOUT_SECONDS = 8.0

_log = logging.getLogger(__name__)


def _within_quota(user_id: int, quota: int, title: str) -> bool:
    today = _dt.date.today().isoformat()
    key = (user_id, today)
    with _quota_lock:
        if (user_id, today, title) in _cheered or _calls.get(key, 0) >= quota:
            return False
        _calls[key] = _calls.get(key, 0) + 1
        _cheered.add((user_id, today, title))
        return True


def encourage(cheer: Cheer, fallback: str, *, user_id: int, quota: int) -> Explanation:
    """完成一題時的鼓勵訊息。失敗、沒 key、超過每日額度就回傳 ``fallback``（範本）。

    ``cheer`` 是 ``core.encouragement.Cheer``。這個函式**不會拋例外**。
    **不要在資料庫交易裡呼叫它**：它會等網路，最多 ``ENCOURAGE_TIMEOUT_SECONDS`` 秒，
    在交易裡等會把 SQLite 的寫入鎖一起抓住。
    """
    template = Explanation(fallback, "static")
    if not is_available() or not _within_quota(user_id, quota, cheer.title):
        return template

    lines = [
        f"題目：{cheer.title}",
        f"主題：{cheer.topic_name}",
        f"難度：{cheer.difficulty}",
    ]
    if cheer.est_minutes and cheer.actual_minutes is not None:
        lines.append(f"預估 {cheer.est_minutes} 分鐘，實際 {cheer.actual_minutes} 分鐘")
    if cheer.today_total:
        lines.append(f"今天的計畫：完成 {cheer.today_done} / {cheer.today_total} 題")

    try:
        import anthropic

        client = anthropic.Anthropic(timeout=ENCOURAGE_TIMEOUT_SECONDS, max_retries=0)
        response = client.beta.messages.create(
            model=ENCOURAGE_MODEL,
            max_tokens=ENCOURAGE_MAX_TOKENS,
            output_config={"effort": "low"},
            betas=[_FALLBACK_BETA],
            fallbacks="default",
            system=_ENCOURAGE_SYSTEM,
            messages=[{"role": "user", "content": "\n".join(lines)}],
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            return template
        text = clean_generated("".join(b.text for b in response.content if b.type == "text"))
        return Explanation(text, "llm") if text else template
    except Exception as exc:
        # 同 explain_task：外部服務的任何問題都不該擋住「打勾完成」這件事。
        # 但要留一行紀錄，否則 key 失效之類的問題會讓功能永遠默默退回範本。
        # 只記類型與狀態碼——不記 request 或 headers，那裡面有 API key。
        _log.warning("encourage 退回範本：%s %s", type(exc).__name__, getattr(exc, "status_code", ""))
        return template
