"""從 JD 自動找社群面經：Claude 用網路搜尋讀 PTT、Dcard、Threads 等公開貼文，回報考了什麼。

為什麼交給 Claude 的 web search 工具，不自己爬：

* **不違反社群的規則**：Dcard 會擋機器人、Threads 要登入，自己寫爬蟲要繞過這些。
  web search 是搜尋引擎已經公開收錄的內容，由 Anthropic 那邊去查。
* **出處對得回原文**：每一句結論都附 ``web_search_result_location`` 引用（網址、標題、
  原文最多 150 字）。這裡**只收**引用網址真的出現在這次搜尋結果裡、而且網域在白名單內的結論，
  模型自己編出來的網址會被丟掉。存進資料庫的「原文」是引用的那段原文，不是 Claude 的改寫；
  Claude 的整理另外存、畫面上標明。

代價：每次要花錢（每次搜尋 US$0.01 加上讀結果的 token），所以每人每天有次數上限；
搜尋引擎沒收錄的貼文（例如 IG 限動、要登入才看得到的 Threads）找不到。

這個模組跟 ``llm/client.py`` 一樣**不拋例外**：沒 key、額度用完、網路錯誤，都回傳一份
說明原因的空報告，頁面照常顯示。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from llm import client as llm_client

_log = logging.getLogger(__name__)

#: 搜尋與整理都要判斷「這篇是不是在講同一間公司、同一種職缺的考試」，需要推理能力，用 Opus。
COMMUNITY_MODEL = "claude-opus-5-5"

#: 只在這些網站裡找。都是有求職心得的公開社群或筆記站；網域也包含子網域。
COMMUNITY_DOMAINS: tuple[str, ...] = (
    "dcard.tw",
    "ptt.cc",
    "threads.com",
    "threads.net",
    "goodjob.life",
    "hackmd.io",
    "medium.com",
)

#: 一次最多搜尋幾次。找一間公司的考試心得，換三五種說法就夠了；也是每次花費的上限。
MAX_SEARCHES = 5

#: 每位使用者每天最多找幾次（一次＝一份 JD）。
COMMUNITY_DAILY_QUOTA = 3

#: 一次最多等多久。搜尋加閱讀常要半分鐘以上。
TIMEOUT_SECONDS = 150.0

#: 送進去的 JD 最多幾個字。判斷公司與職缺用不到整份。
MAX_JD_CHARS_SENT = 4000

#: 搜尋一輪可能被暫停（``pause_turn``），最多接著跑幾次。
MAX_CONTINUATIONS = 3

_SYSTEM = """你是求職研究助理。使用者貼了一份職缺描述（JD），想知道這間公司（或同公司相近職缺）的\
程式能力測驗與面試，社群上的人實際遇到了什麼。

做法：
1. 從 JD 判斷公司與職缺類型。看不出公司就用職缺類型與產業去找。
2. 用 web_search 搜尋社群上的面試或線上測驗心得（中文與英文關鍵字都試）。
3. 只回報貼文裡真的寫到的考試資訊：題數、時間、難度、題型（例如陣列、字串、Tree、Linked list、DP、\
greedy、two pointer、binary search）、作答語言限制、平台操作的坑、面試會問什麼。

規則：
- 用繁體中文，一點一句，每一句都要引用來源。
- 找不到相關心得就直接說找不到，不要用一般常識或自己的印象補。
- 不同年份或職缺的說法不一致時，照實寫出差異。
- 不要寫出貼文作者的個人資料。"""


@dataclass(frozen=True)
class Source:
    """一處引用：網址、頁面標題、引用的原文（最多 150 字）。"""

    url: str
    title: str
    cited_text: str


@dataclass
class Finding:
    """Claude 整理的一句結論，以及它引用的原文。"""

    claim: str
    sources: list[Source]


@dataclass
class CommunityReport:
    """一次搜尋的結果。``status`` 是 ok／empty／unavailable／quota／error。"""

    status: str
    message: str = ""
    findings: list[Finding] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    searches: int = 0

    def by_url(self) -> dict[str, tuple[str, list[str], list[str]]]:
        """網址 → (標題, 引用的原文們, 引用它的結論們)。存檔用：一個網址存成一篇面經。"""
        out: dict[str, tuple[str, list[str], list[str]]] = {}
        for finding in self.findings:
            for src in finding.sources:
                title, texts, claims = out.setdefault(src.url, (src.title, [], []))
                if src.cited_text and src.cited_text not in texts:
                    texts.append(src.cited_text)
                if finding.claim not in claims:
                    claims.append(finding.claim)
        return out


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    scheme_ok = urlparse(url).scheme in ("http", "https")
    return scheme_ok and any(host == d or host.endswith("." + d) for d in COMMUNITY_DOMAINS)


def _field(obj, name, default=None):  # noqa: ANN001, ANN202
    """SDK 回傳的是物件，測試用 dict；兩種都讀得到。"""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def parse_response(content: list) -> tuple[list[Finding], list[str], set[str]]:
    """從回應的內容區塊整理出：結論（只留有效引用的）、搜尋關鍵字、這次搜尋結果的網址。

    有效引用＝引用網址真的出現在這次的搜尋結果裡，而且在白名單網域內。
    搜尋結果在後面、引用在前面也沒關係：先收集完網址再過濾。
    """
    queries: list[str] = []
    result_urls: set[str] = set()
    raw: list[tuple[str, list]] = []
    for block in content:
        kind = _field(block, "type")
        if kind == "server_tool_use" and _field(block, "name") == "web_search":
            query = (_field(block, "input") or {}).get("query")
            if query:
                queries.append(str(query))
        elif kind == "web_search_tool_result":
            results = _field(block, "content")
            if isinstance(results, list):  # 出錯時是單一個錯誤物件，不是清單
                for r in results:
                    url = _field(r, "url")
                    if url:
                        result_urls.add(url)
        elif kind == "text":
            citations = _field(block, "citations") or []
            if citations:
                raw.append(((_field(block, "text") or "").strip(), citations))

    findings: list[Finding] = []
    for claim, citations in raw:
        sources = []
        for c in citations:
            if _field(c, "type") != "web_search_result_location":
                continue
            url = _field(c, "url") or ""
            if url in result_urls and _allowed(url):
                sources.append(
                    Source(url=url, title=(_field(c, "title") or url)[:120], cited_text=(_field(c, "cited_text") or "").strip())
                )
        if claim and sources:
            findings.append(Finding(claim=claim, sources=sources))
    return findings, queries, result_urls


def search_community(jd_text: str, *, user_id: int, hint: str = "", client=None) -> CommunityReport:  # noqa: ANN001
    """用 JD 去找社群面經。``client`` 給測試注入假的 Anthropic client。

    **不要在資料庫交易裡呼叫**：會等網路最多 ``TIMEOUT_SECONDS`` 秒。
    """
    if client is None and not llm_client.is_available():
        return CommunityReport(
            "unavailable",
            "這個功能要用 Claude 搜尋網路，需要設定 ANTHROPIC_API_KEY（公開展示版不開放，避免陌生人花掉部署者的錢）。",
        )
    if llm_client._reserve(user_id, "community", 1, COMMUNITY_DAILY_QUOTA) < 1:
        return CommunityReport("quota", f"今天已經找過 {COMMUNITY_DAILY_QUOTA} 次了，明天再試。")

    prompt = f"JD：\n{jd_text[:MAX_JD_CHARS_SENT]}"
    if hint.strip():
        prompt = f"補充（公司或職缺）：{hint.strip()[:200]}\n\n" + prompt
    messages: list = [{"role": "user", "content": prompt}]
    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic(timeout=TIMEOUT_SECONDS, max_retries=1)
        content: list = []
        searches = 0
        for _ in range(MAX_CONTINUATIONS + 1):
            response = client.beta.messages.create(
                model=COMMUNITY_MODEL,
                max_tokens=16000,
                output_config={"effort": "medium"},
                betas=[llm_client._FALLBACK_BETA],
                fallbacks="default",
                system=_SYSTEM,
                tools=[
                    {
                        "type": "web_search_20260318",
                        "name": "web_search",
                        "max_uses": MAX_SEARCHES,
                        "allowed_domains": list(COMMUNITY_DOMAINS),
                        "user_location": {"type": "approximate", "country": "TW", "timezone": "Asia/Taipei"},
                    }
                ],
                messages=messages,
            )
            content.extend(response.content)
            usage = _field(_field(response, "usage"), "server_tool_use")
            searches += int(_field(usage, "web_search_requests", 0) or 0)
            if response.stop_reason != "pause_turn":
                break
            # 搜尋太久被暫停：把暫停前的內容原封不動送回去，接著跑
            messages = [*messages, {"role": "assistant", "content": response.content}]
        if response.stop_reason == "refusal":
            return CommunityReport("error", "Claude 拒絕處理這份 JD，沒有搜尋結果。", searches=searches)
    except Exception as exc:  # noqa: BLE001
        llm_client._refund(user_id, "community", 1)  # 沒搜成，次數退回
        # 只記類型與狀態碼：request 與 headers 裡有 API key
        _log.warning("社群搜尋失敗：%s %s", type(exc).__name__, getattr(exc, "status_code", ""))
        return CommunityReport("error", "搜尋失敗（網路或 Claude 服務的問題），這次不算次數，稍後再試。")

    findings, queries, _urls = parse_response(content)
    if not findings:
        return CommunityReport(
            "empty", "搜尋了，但沒有找到有引用來源的考試心得。可以在補充欄寫上公司或職缺名稱再試一次。",
            queries=queries, searches=searches,
        )
    return CommunityReport("ok", findings=findings, queries=queries, searches=searches)
