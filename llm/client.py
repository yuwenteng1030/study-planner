"""Claude API 的最小封裝：產生「為什麼該做這一項」。

**設計上的硬要求：LLM 掛掉時功能不能減。**

所以這個模組只做一件事——有 API key 就生成客製的說明，沒有、額度用完、
或呼叫失敗，就回退到 ``tasks.static_note`` 預存的那句話。使用者看到的差別
只是文字從客製變罐頭，計畫照排、進度照記、網站照用。

這也是為什麼 ``static_note`` 是題庫的必填欄位，而不是「有空再補」。

刻意**不做**的事（都排在 v0.1 之後）：快取、重試、成本統計、串流。
v0.1 的目標是跑得起來且不會因為外部服務而壞掉，不是跑得省。
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: 用哪個模型。選 Haiku 是因為這個任務是「把已知重點改寫成一句話」，
#: 不需要推理能力，而便宜與快才直接影響使用體驗。
DEFAULT_MODEL = "claude-3-5-haiku-20241022"

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
) -> Explanation:
    """產生「為什麼該做這一題」。

    任何失敗都回退到 ``static_note``；``static_note`` 也沒有才回傳空字串。
    這個函式**不會拋例外**——呼叫端不需要包 try。
    """
    fallback = Explanation(static_note, "static") if static_note else Explanation("", "none")

    if not is_available():
        return fallback

    try:
        import anthropic

        client = anthropic.Anthropic()
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
        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            return fallback
        return Explanation(text, "llm")
    except Exception:
        # 這裡刻意吞掉所有例外。外部服務的任何問題——沒安裝套件、
        # key 失效、額度用完、超時、回傳格式變了——都不該讓使用者
        # 看不到今天的任務。
        return fallback


def explain_many(
    tasks: list[dict],
    model: str = DEFAULT_MODEL,
) -> dict[int, Explanation]:
    """一次處理多題。

    ``tasks`` 每筆需要 ``id``／``title``／``topic_name``／``key_points``／
    ``static_note``。逐題呼叫，不做批次——v0.1 一份計畫只有 20 幾題，
    批次的複雜度換不到有感的速度。
    """
    return {
        task["id"]: explain_task(
            title=task["title"],
            topic_name=task.get("topic_name", ""),
            key_points=task.get("key_points") or [],
            static_note=task.get("static_note"),
            model=model,
        )
        for task in tasks
    }
