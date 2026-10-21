"""目標預設選項。

v0.1 的表單是**下拉選單**而不是自由文字框，原因是自由文字必須先經過 LLM
才能變成主題清單，而 LLM 一掛，整個表單就送不出去——那會讓「核心功能
不依賴外部服務」這條原則破功。下拉選單把目標解析從執行期移到設計期：
選項與主題的對應關係在這裡寫死，離線也能用。

自由文字目標（``goal_parser``）排在 v0.1 之後。到時候這份對照表會變成
LLM 失敗時的備援清單，而不是被取代。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoalPreset:
    """一個目標選項，以及它對應到哪些主題。"""

    id: str
    label: str

    #: 命中 ``goal_factor``（×1.3）的主題。
    topics: tuple[str, ...]

    description: str = ""


#: 選項刻意不寫任何公司或單位名稱——這個專案的 repository 是公開的。
#: 選項的設計邏輯是「考試型態」而不是「哪一家公司」，
#: 這樣對其他人也有用，而且不必隨著我自己的求職進度修改程式碼。
GOAL_PRESETS: tuple[GoalPreset, ...] = (
    GoalPreset(
        id="coding_test_basic",
        label="程式上機測驗（Python 基礎與資料結構）",
        topics=(
            "python_basic",
            "arrays",
            "strings",
            "hashmaps",
            "sorting",
            "search",
            "greedy",
            "stacks_queues",
        ),
        description=(
            "以一小時內寫得完的題型為主：字串處理、計數、排序、雙指標。"
            "多數企業的線上程式測驗考的就是這個範圍。"
        ),
    ),
    GoalPreset(
        id="ds_algo_foundation",
        label="資料結構與演算法入門",
        topics=(
            "python_basic",
            "time_complexity",
            "arrays",
            "linked_lists",
            "stacks_queues",
            "hashmaps",
            "sorting",
            "search",
        ),
        description="從時間複雜度開始，把基本資料結構走完一輪。",
    ),
    GoalPreset(
        id="advanced_algo",
        label="進階演算法（遞迴、樹、圖、動態規劃）",
        topics=("recursion", "trees", "graphs", "heaps", "dp_1d", "dp_2d", "tries"),
        description="難度較高的題型。建議先把入門範圍做完再選這項。",
    ),
    GoalPreset(
        id="balanced",
        label="不特別偏好，按重要性排",
        topics=(),
        description=(
            "不加任何目標權重，純粹按主題重要性與你的自評程度排序。"
            "不確定要準備什麼的時候選這個。"
        ),
    ),
)

GOAL_BY_ID: dict[str, GoalPreset] = {preset.id: preset for preset in GOAL_PRESETS}
GOAL_BY_LABEL: dict[str, GoalPreset] = {
    preset.label: preset for preset in GOAL_PRESETS
}
