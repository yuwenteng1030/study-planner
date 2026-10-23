"""完成一題時的鼓勵訊息：範本版。

這裡只有範本，不碰 LLM。``llm/client.encourage`` 用 Claude 生成客製的那句，
失敗、沒有 API key、或超過每日額度時就退回這裡——鼓勵訊息跟「為什麼該做這題」
一樣，外部服務掛掉時功能不能減，只是文字從客製變罐頭。

範本按情境挑，**依序比對、第一個符合的勝出**：
今天全做完 → 拿下 Hard → 比預估快 → 花得比預估久 → 一般。
「今天全做完」排第一，是因為那是一天裡最值得說的一件事；
「花得久」排在「比預估快」後面，兩者不會同時成立，順序只是讓規則好讀。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 實際分鐘數超過預估的這個倍數，才算「花得比預估久」。
#: 剛好超過一點不值得特別安慰，反而像在提醒使用者「你比較慢」。
SLOW_RATIO = 1.5


@dataclass(frozen=True)
class Cheer:
    """剛完成的那一題，以及寫鼓勵訊息需要的情境。"""

    title: str
    topic_name: str
    difficulty: str
    est_minutes: int | None = None
    actual_minutes: int | None = None
    #: 今天的計畫做完幾題、共幾題。不在計畫裡（例如從地圖做的）就是 None。
    today_done: int | None = None
    today_total: int | None = None

    @property
    def all_done_today(self) -> bool:
        return bool(self.today_total) and self.today_done is not None and self.today_done >= self.today_total

    @property
    def left_today(self) -> int | None:
        if self.today_total is None or self.today_done is None:
            return None
        return max(0, self.today_total - self.today_done)


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>$~:])")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")

#: Claude 寫的那句最多留幾個字。系統提示要它寫 30–60 字，這是硬上限。
MAX_GENERATED_CHARS = 120


def clean_generated(text: str) -> str:
    """Claude 寫的鼓勵只留純文字的第一行：拿掉 Markdown 連結與網址，截到上限。

    ``st.toast`` 會把內容當 Markdown 畫，連結點得到；鼓勵訊息不該帶任何連結。
    """
    text = _MD_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:MAX_GENERATED_CHARS]


def toast_text(text: str) -> str:
    """放進 ``st.toast`` 前跳脫 Markdown：題名裡的 ``*``、``_``、``[`` 不能變成格式或連結。"""
    return _MD_SPECIAL.sub(r"\\\1", text)


def template_message(cheer: Cheer) -> str:
    """依情境挑一句範本。永遠回傳非空字串。"""
    name = f"「{cheer.title}」"
    if cheer.all_done_today:
        return f"今天排的 {cheer.today_total} 題全部做完了！{name}是今天的最後一關，好好休息。"
    if cheer.difficulty == "Hard":
        return f"Hard 題{name}拿下了。上機測驗的 Hard 就是拉開差距的那一題，這種題多做幾題就不怕了。"
    est, actual = cheer.est_minutes, cheer.actual_minutes
    if est and actual is not None and actual < est:
        return f"{name}比預估快了 {est - actual} 分鐘完成，{cheer.topic_name} 越來越順手了。"
    if est and actual is not None and actual > est * SLOW_RATIO:
        return f"{name}花得比預估久，但做完就是做完了。卡住的地方正是這一題要教你的。"
    left = cheer.left_today
    tail = f"今天還剩 {left} 題。" if left else "繼續保持。"
    return f"又完成一題 {cheer.topic_name}：{name}。{tail}"
