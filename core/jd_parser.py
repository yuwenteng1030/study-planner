"""把職缺描述（JD）拆成「提到哪些主題、原文是哪一句」。

為什麼用關鍵字比對，不用 Claude：

* **出處必須是原文**。這個功能的賣點是「每個主題附出處」，引用的那一句一定要真的
  出現在使用者貼的 JD 裡。關鍵字比對引用的就是原文的那一句；讓模型挑句子，
  它可能改寫、合併，甚至編一句出來，還得另外驗證。
* **可重現、可測試**：同一份 JD 永遠得到同一組結果，``jd_factor`` 才跟評分公式一樣有 pytest。
* **不花錢、不依賴網路**：公開展示版不呼叫 Claude，這個功能照樣能用。

代價是漏抓與誤判：JD 寫「處理大量交易資料」不會被算成 arrays。所以只收**明確的詞**，
寧可漏抓也不要亂抓——``jd_factor`` 會改變排序，誤判比漏抓傷害大。
「資料結構」「演算法」這種泛稱不算進任何一個主題（全部加分等於沒加），另外列出來。

這個模組是純函式，不碰資料庫。
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from core.scoring import JDStats

#: 一份 JD 最多收幾個字。貼整頁網頁進來也不會拖垮比對。
MAX_JD_CHARS = 20_000

#: 引用句太長時，關鍵字前後各留幾個字。
QUOTE_CONTEXT = 60


def _en(*words: str, case: bool = False) -> tuple[str, ...]:
    """英文詞：前後不能緊接英數字，避免 ``tree`` 比對到 ``street``、``heap`` 比對到 ``cheap``。

    詞裡的空白也接受連字號（``linked list``＝``linked-list``）。預設不分大小寫；
    ``case=True`` 給專有名詞用：``React``、``Spring``、``AI`` 分大小寫，
    「react quickly」「spring 招募」才不會被當成技能。
    """
    flag = "" if case else "(?i)"
    return tuple(rf"{flag}(?<![A-Za-z0-9]){w.replace(' ', '[- ]')}(?![A-Za-z0-9])" for w in words)


def _zh(*words: str) -> tuple[str, ...]:
    return tuple(re.escape(w) for w in words)


#: 主題 → 會被算成「提到這個主題」的詞。只放明確的詞，理由見模組說明。
TOPIC_PATTERNS: dict[str, tuple[str, ...]] = {
    "python_basic": _en(r"python ?3?(?:\.\d+)?", r"python3"),
    "time_complexity": _en(r"time complexity", r"big ?o", r"space complexity")
    + _zh("時間複雜度", "空間複雜度"),
    # 單數 array 後面接 of 是「一系列」（a wide array of tools），不算
    "arrays": _en(r"arrays", r"array(?! of)", r"sliding window", r"two pointers?", r"prefix sums?") + _zh("陣列"),
    "strings": _en(r"string manipulation", r"string processing", r"text processing", r"regex", r"regular expressions?")
    + _zh("字串處理", "字串", "文字處理", "正規表示式", "正則表達式"),
    "hashmaps": _en(r"hash ?maps?", r"hash tables?", r"hashing", r"dictionar(?:y|ies)") + _zh("雜湊", "哈希"),
    "sorting": _en(r"sorting", r"sort algorithms?") + _zh("排序"),
    "search": _en(r"binary search", r"search algorithms?") + _zh("二分搜尋", "二元搜尋", "搜尋演算法"),
    "greedy": _en(r"greedy") + _zh("貪婪", "貪心"),
    # 技術棧（tech stack、ELK stack、full-stack）與訊息系統（message queue、Kafka queue system）
    # 都不是資料結構
    "stacks_queues": (
        r"(?i)(?<![A-Za-z0-9])(?<!tech )(?<!tech-)(?<!full )(?<!full-)(?<!technology )(?<!elk )(?<!software )"
        r"stacks?(?![A-Za-z0-9-])(?! (?:overflow|developer|engineer))",
        r"(?i)(?<![A-Za-z0-9])(?<!message )(?<!task )(?<!job )(?<!kafka )(?<!rabbitmq )(?<!work )"
        r"queues?(?![A-Za-z0-9])(?! (?:systems?|services?))",
    )
    + _zh("堆疊", "佇列"),
    "linked_lists": _en(r"linked lists?") + _zh("鏈結串列", "鏈表"),
    "recursion": _en(r"recursion", r"recursive", r"backtracking") + _zh("遞迴", "回溯"),
    # decision tree 是機器學習模型，family tree、source tree 也不是資料結構
    "trees": _en(r"binary trees?", r"BST")
    + (r"(?i)(?<![A-Za-z0-9])(?<!decision )(?<!family )(?<!source )(?<!directory )trees?(?![A-Za-z0-9])",)
    + _zh("二元樹", "二叉樹", "樹狀結構"),
    "heaps": _en(r"heaps?", r"priority queues?") + _zh("優先佇列", "堆積"),
    "graphs": _en(r"graphs?", r"graph theory", r"BFS", r"DFS", r"shortest paths?") + _zh("圖論", "最短路徑"),
    "dp_1d": _en(r"dynamic programming") + _zh("動態規劃"),
    "dp_2d": _en(r"dynamic programming") + _zh("動態規劃"),
    "two_d_arrays": _en(r"matri(?:x|ces)", r"2d arrays?", r"grids?") + _zh("矩陣", "二維陣列"),
    "bit_manipulation": _en(r"bit manipulation", r"bitwise", r"bit operations?") + _zh("位元運算"),
    "math_number_theory": _en(r"number theory", r"prime numbers?", r"combinatorics") + _zh("數論", "質數", "排列組合"),
    "tries": _en(r"tries", r"trie", r"prefix trees?") + _zh("字典樹"),
}

#: 泛稱：每個主題都沾得上邊，算進任何一個都不公平，另外列出來。
GENERIC_PATTERNS: tuple[str, ...] = _en(
    r"data structures?", r"algorithms?", r"DSA", r"leetcode", r"hackerrank", r"coding tests?"
) + _zh("資料結構", "演算法", "程式能力測驗", "上機考", "線上測驗")

#: JD 常見、但題庫沒有練到的技能。列出來讓使用者知道「刷題之外還要準備什麼」。
OTHER_SKILLS: dict[str, tuple[str, ...]] = {
    "SQL／資料庫": _en(r"SQL", r"MySQL", r"PostgreSQL", r"MSSQL", r"databases?") + _en(r"Oracle", case=True) + _zh("資料庫"),
    "Linux／Shell": _en(r"Linux", r"Unix", r"shell scripts?", r"bash"),
    "Git／版本控制": _en(r"git", r"GitHub", r"GitLab", r"version control") + _zh("版本控制"),
    "Docker／Kubernetes": _en(r"docker", r"kubernetes", r"k8s") + _zh("容器"),
    "雲端": _en(r"AWS", r"Azure", r"GCP", r"cloud") + _zh("雲端"),
    "Web API": _en(r"REST(?:ful)?", r"APIs?", r"web services?", r"microservices?") + _zh("微服務"),
    "前端": _en(r"JavaScript", r"TypeScript", r"HTML", r"CSS") + _en(r"React", r"Vue", r"Angular", case=True) + _zh("前端"),
    "Java／C#／.NET": _en(r"Java", r"C#", r"Spring", r"\.NET", case=True) + (r"(?i)ASP\.NET",),
    # 單獨一個「C」太容易誤判（Plan C、C 棟），只認 C++ 與「C 語言」
    "C／C++": _en(r"C\+\+", r"C(?= ?語言)", case=True),
    "AI／機器學習": _en(r"machine learning", r"deep learning") + _en(r"AI", r"LLMs?", r"NLP", case=True)
    + _zh("機器學習", "深度學習", "人工智慧"),
    "資料分析": _en(r"pandas", r"data analysis", r"Power BI", r"Tableau", r"ETL") + _zh("資料分析", "數據分析"),
    "英文": _en(r"English", r"TOEIC", case=True) + _zh("英文", "多益"),
}


@dataclass(frozen=True)
class Mention:
    """JD 裡的一處引用。``quote[start:end]`` 就是比對到的那個詞。"""

    keyword: str
    quote: str
    start: int
    end: int


@dataclass
class JDAnalysis:
    """一份 JD 的分析結果。每個主題、每項技能只留第一處引用。"""

    topics: dict[str, Mention] = field(default_factory=dict)
    generic: list[Mention] = field(default_factory=list)
    other_skills: dict[str, Mention] = field(default_factory=dict)


_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])|\n|(?<=\.)\s+")
#: 行首的條列符號：「- 」「• 」「1. 」「(2)」「3、」。不能把數字整個吃掉，「3+ years」的 3 要留著。
_BULLET = re.compile(r"^\s*(?:[\-–—•·*●▪◆◇○‧►>]+|\d+[.)、](?!\d)|[（(]\d+[)）])\s*")


def sentences(text: str) -> list[str]:
    """把 JD 切成句子。條列符號拿掉，空行丟掉。"""
    out = []
    for raw in _SENTENCE_SPLIT.split(text[:MAX_JD_CHARS]):
        line = _BULLET.sub("", raw or "").strip()
        if line:
            out.append(line)
    return out


def _clip(sentence: str, start: int, end: int) -> Mention:
    """句子太長就只留關鍵字前後一段，前後補上「…」。"""
    keyword = sentence[start:end]
    if len(sentence) <= QUOTE_CONTEXT * 2 + len(keyword):
        return Mention(keyword, sentence, start, end)
    left = max(0, start - QUOTE_CONTEXT)
    right = min(len(sentence), end + QUOTE_CONTEXT)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(sentence) else ""
    quote = prefix + sentence[left:right] + suffix
    offset = len(prefix) - left
    return Mention(keyword, quote, start + offset, end + offset)


def _first(patterns: Iterable[str], lines: list[str]) -> Mention | None:
    """依句子順序找第一個比對到的詞（同一句裡取最靠前的）。"""
    compiled = [re.compile(p) for p in patterns]
    for line in lines:
        hits = [m for c in compiled if (m := c.search(line))]
        if hits:
            best = min(hits, key=lambda m: (m.start(), -m.end()))
            return _clip(line, best.start(), best.end())
    return None


@functools.lru_cache(maxsize=512)
def analyze(text: str) -> JDAnalysis:
    """分析一份 JD。

    結果依原文快取：Streamlit 每點一下就重跑整頁，同一份 JD 會被分析好幾次，
    一份兩萬字約 50 毫秒，30 份就是 1.5 秒。呼叫端不能修改回傳的物件。
    """
    lines = sentences(text)
    result = JDAnalysis()
    for topic_id, patterns in TOPIC_PATTERNS.items():
        if mention := _first(patterns, lines):
            result.topics[topic_id] = mention
    compiled = [re.compile(p) for p in GENERIC_PATTERNS]
    for line in lines:
        hits = [m for c in compiled if (m := c.search(line))]
        if hits:
            best = min(hits, key=lambda m: m.start())
            result.generic.append(_clip(line, best.start(), best.end()))
    for label, patterns in OTHER_SKILLS.items():
        if mention := _first(patterns, lines):
            result.other_skills[label] = mention
    return result


def jd_stats(analyses: Iterable[JDAnalysis]) -> JDStats:
    """多份 JD → 評分公式要的 ``JDStats``：總份數，以及每個主題被幾份提到。

    一份 JD 提到同一個主題十次也只算一份——``jd_factor`` 量的是「多少比例的職缺要這個」，
    不是「誰寫得比較囉嗦」。
    """
    analyses = list(analyses)
    hits: dict[str, int] = {}
    for a in analyses:
        for topic_id in a.topics:
            hits[topic_id] = hits.get(topic_id, 0) + 1
    return JDStats(total=len(analyses), hits=hits)


_MD_SPECIAL = set("\\`*_{}[]()#+-.!|<>~$:")


def md_escape(text: str) -> str:
    """JD 是使用者貼的文字，放進 Streamlit 的 markdown 前把語法字元跳脫掉。

    不跳脫的話，JD 裡的 ``[按這裡](網址)`` 會變成可以點的連結、``:red[...]`` 會變色。
    """
    return "".join("\\" + c if c in _MD_SPECIAL else c for c in text).replace("\n", " ")


def highlight_markdown(mention: Mention) -> str:
    """引用句轉成 markdown，比對到的詞加粗，其餘全部跳脫。"""
    q = mention.quote
    return f"{md_escape(q[: mention.start])}**{md_escape(q[mention.start : mention.end])}**{md_escape(q[mention.end :])}"
