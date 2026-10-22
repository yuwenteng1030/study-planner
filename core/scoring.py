"""主題評分與優先度分級。

這個模組是整個專案「不只是問 ChatGPT」最硬的證據：**同樣的輸入一定得到
同樣的輸出**，而且每個數字都解釋得出來。所以它刻意是純函式——不碰資料庫、
不碰 HTTP、不呼叫 LLM。要測它只需要傳入幾個 dict。

公式（規格來源：``學習網頁架構設計.md`` §4）::

    score(topic) = base_weight × jd_factor × gap_factor × goal_factor

四個因子的角色不同，所以用**乘法**而不是加法：加法會讓「完全沒提到的主題」
和「每份 JD 都提到的主題」只差一個常數，乘法才能讓它們按比例拉開。
代價是任何因子為 0 都會讓整個分數歸零，因此下面每個因子都有明確的下界。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 常數：這些數字是設計決策，不是隨便填的。改動前先讀註解。
# ---------------------------------------------------------------------------

#: ``jd_factor`` 的上下界。
#:
#: 下界 1.0：沒有任何 JD 時公式照樣要跑得出合理結果（架構設計 §10 的硬要求）。
#: 若下界設 0，冷啟動時所有分數都會是 0，網站第一天就不能用。
#:
#: 上界 2.0：代表「JD 的影響力最多只能讓一個主題的分數翻倍」。
#: 設 3 會讓 JD 蓋過 base_weight——一個 base_weight=3 的冷門主題只要每份 JD
#: 都提到，就會贏過 base_weight=8 的核心主題（3×3=9 > 8×1=8）。
#: 設 2 的話 3×2=6 < 8×1=8，JD 只能排序同級主題，不能顛覆基本盤。
#:
#: 注意：目前 ``jd_factor()`` 的 ``1 + ratio`` 因為 ratio ≤ 1，本來就 ≤ 2，
#: 所以這個上界是公式的形狀給的，``min()`` 夾鉗從未生效（見 README 設計決策 2）。
JD_FACTOR_MIN = 1.0
JD_FACTOR_MAX = 2.0

#: ``gap_factor``：使用者自評程度對應的權重。
#:
#: 已熟 0.3 而不是 0：已經熟的主題仍該偶爾複習，給 0 會讓它永遠排不進計畫。
#: 沒學過 1.5：拉高但不過度，因為「沒學過」不等於「最該先學」——
#: 真正的先後由 base_weight 和前置關係決定，gap 只是修正。
GAP_FACTORS: dict[str, float] = {
    "familiar": 0.3,
    "normal": 1.0,
    "new": 1.5,
}
DEFAULT_GAP_FACTOR = GAP_FACTORS["normal"]

#: ``goal_factor``：命中使用者目標時的加權。
#:
#: 1.3 而不是 2.0：目標是「偏好」不是「硬條件」。給 2.0 會讓目標主題
#: 直接霸佔整張必學清單，使用者就看不到自己沒想到但其實重要的主題——
#: 那恰好是這個網站存在的理由。
GOAL_FACTOR_HIT = 1.3
GOAL_FACTOR_MISS = 1.0

#: 百分位分級的門檻：前 40% 必學、次 35% 建議、其餘加分。
#:
#: 用百分位而不是寫死分數門檻（例如「score > 10 就是必學」），
#: 是因為換領域、調 base_weight、或使用者程度改變時，分數的絕對範圍會整個平移，
#: 寫死的門檻每次都要重調。百分位讓分級自動跟著分布走。
TIER_MUST_RATIO = 0.40
TIER_RECOMMENDED_RATIO = 0.35

TIER_MUST = "must"
TIER_RECOMMENDED = "recommended"
TIER_BONUS = "bonus"


# ---------------------------------------------------------------------------
# 輸入與輸出的資料結構
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopicInput:
    """評分需要的主題資料。刻意跟 ORM 物件脫鉤，方便測試。"""

    id: str
    name: str
    base_weight: int


@dataclass(frozen=True)
class UserContext:
    """使用者這一側的輸入。"""

    #: 主題 id -> 自評程度（``familiar`` / ``normal`` / ``new``）。
    #: 沒填的主題一律當 ``normal``。
    levels: dict[str, str] = field(default_factory=dict)

    #: 命中使用者目標的主題 id。v0.1 由下拉選單直接給一組固定清單，
    #: 不經 LLM 解析（``goal_parser`` 排在 v0.2）。
    goal_topics: frozenset[str] = frozenset()


@dataclass(frozen=True)
class JDStats:
    """JD 統計。v0.1 一律是空的，冷啟動路徑就是主路徑。"""

    #: 貼進來的 JD 總份數。
    total: int = 0

    #: 主題 id -> 提到該主題的 JD 份數。
    hits: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredTopic:
    """單一主題的評分結果。

    四個因子都保留下來，不只存最後的 score——因為畫面上要能回答
    「為什麼這一項排第一」，而且面試時要能逐項拆解。
    """

    topic_id: str
    name: str
    score: float
    base_weight: int
    jd_factor: float
    gap_factor: float
    goal_factor: float
    priority: str = TIER_BONUS

    def explain(self) -> str:
        """一句話說明這個分數怎麼來的。"""
        parts = [f"主題重要性 {self.base_weight}"]
        if self.jd_factor > JD_FACTOR_MIN:
            pct = round((self.jd_factor - 1) * 100)
            parts.append(f"{pct}% 的 JD 提到")
        if self.gap_factor != DEFAULT_GAP_FACTOR:
            parts.append("你還沒學過" if self.gap_factor > 1 else "你已經熟了")
        if self.goal_factor > GOAL_FACTOR_MISS:
            parts.append("命中你的目標")
        return "、".join(parts) + f"　→　{self.score:.2f}"


# ---------------------------------------------------------------------------
# 各因子
# ---------------------------------------------------------------------------


def jd_factor(topic_id: str, jd: JDStats) -> float:
    """``1 + (提到該主題的 JD 份數 ÷ 總份數)``，夾在 1.0–2.0。

    沒有任何 JD 時回傳 1.0，公式照樣跑得動。
    """
    if jd.total <= 0:
        return JD_FACTOR_MIN
    ratio = jd.hits.get(topic_id, 0) / jd.total
    return min(max(JD_FACTOR_MIN + ratio, JD_FACTOR_MIN), JD_FACTOR_MAX)


def gap_factor(topic_id: str, user: UserContext) -> float:
    """依自評程度回傳權重；沒填的主題當 ``normal``。"""
    return GAP_FACTORS.get(user.levels.get(topic_id, "normal"), DEFAULT_GAP_FACTOR)


def goal_factor(topic_id: str, user: UserContext) -> float:
    """命中目標回傳 1.3，否則 1.0。"""
    return GOAL_FACTOR_HIT if topic_id in user.goal_topics else GOAL_FACTOR_MISS


# ---------------------------------------------------------------------------
# 主要進入點
# ---------------------------------------------------------------------------


def score_topic(
    topic: TopicInput, user: UserContext, jd: JDStats
) -> ScoredTopic:
    """算出單一主題的分數與四個因子。"""
    jdf = jd_factor(topic.id, jd)
    gapf = gap_factor(topic.id, user)
    goalf = goal_factor(topic.id, user)
    return ScoredTopic(
        topic_id=topic.id,
        name=topic.name,
        score=topic.base_weight * jdf * gapf * goalf,
        base_weight=topic.base_weight,
        jd_factor=jdf,
        gap_factor=gapf,
        goal_factor=goalf,
    )


def assign_priorities(scored: list[ScoredTopic]) -> list[ScoredTopic]:
    """依百分位把主題切成必學／建議／加分三層。

    排序鍵是 ``(-score, topic_id)``：分數相同時用 id 當第二鍵，
    確保**同樣的輸入永遠得到同樣的順序**。少了這一步，Python 的排序
    雖然穩定，但輸入順序一變（例如資料庫回傳順序改了）分級就會跟著跳動,
    golden case 也就測不出東西來。

    門檻用「數量」而不是「分數」切：``int(n × 0.40)`` 筆必學。
    只要有主題就至少給 1 筆必學，否則主題數少於 3 時整張清單會全是加分，
    使用者打開網站看不到任何該做的事。
    """
    if not scored:
        return []

    ordered = sorted(scored, key=lambda s: (-s.score, s.topic_id))
    n = len(ordered)

    must_count = max(1, int(n * TIER_MUST_RATIO))
    recommended_count = int(n * TIER_RECOMMENDED_RATIO)

    result: list[ScoredTopic] = []
    for index, item in enumerate(ordered):
        if index < must_count:
            tier = TIER_MUST
        elif index < must_count + recommended_count:
            tier = TIER_RECOMMENDED
        else:
            tier = TIER_BONUS
        result.append(
            ScoredTopic(
                topic_id=item.topic_id,
                name=item.name,
                score=item.score,
                base_weight=item.base_weight,
                jd_factor=item.jd_factor,
                gap_factor=item.gap_factor,
                goal_factor=item.goal_factor,
                priority=tier,
            )
        )
    return result


def rank_topics(
    topics: list[TopicInput],
    user: UserContext | None = None,
    jd: JDStats | None = None,
) -> list[ScoredTopic]:
    """評分並分級，回傳由高分到低分的清單。

    這是模組的主要進入點。``user`` 與 ``jd`` 都可以省略——
    省略就是冷啟動，結果等同於按 ``base_weight`` 排序。
    """
    user = user or UserContext()
    jd = jd or JDStats()
    return assign_priorities([score_topic(t, user, jd) for t in topics])
