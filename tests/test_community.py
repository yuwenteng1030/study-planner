"""從 JD 自動找社群面經：只收有效引用、額度、暫停後接著跑、失敗退回次數、存檔。

不呼叫真的 Claude：注入假的 client，回傳跟 API 文件一樣形狀的內容區塊。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from db import repositories as repo
from db.models import User
from llm import client as llm_client
from llm import community

DCARD = "https://www.dcard.tw/f/tech_job/p/261629155"
PTT = "https://www.ptt.cc/bbs/Soft_Job/M.1651857325.A.06A.html"


def _response(content, stop_reason="end_turn", searches=1):
    return SimpleNamespace(
        content=content, stop_reason=stop_reason,
        usage=SimpleNamespace(server_tool_use=SimpleNamespace(web_search_requests=searches)),
    )


def _cite(url, text, title="心得"):
    return {"type": "web_search_result_location", "url": url, "title": title, "cited_text": text}


GOOD_CONTENT = [
    {"type": "server_tool_use", "name": "web_search", "input": {"query": "台積電 IT HackerRank 心得"}},
    {"type": "web_search_tool_result", "content": [
        {"type": "web_search_result", "url": DCARD, "title": "台積電IT面試心得跟建議"},
        {"type": "web_search_result", "url": PTT, "title": "台積 IT SRE 面試心得"},
        {"type": "web_search_result", "url": "https://example.com/blog", "title": "別的網站"},
    ]},
    {"type": "text", "text": "三題難度是 Easy、Medium、Medium。",
     "citations": [_cite(DCARD, "考試難度我覺得是EMM")]},
    {"type": "text", "text": "其中一題是 two pointer + binary search。",
     "citations": [_cite(DCARD, "(M) two pointer + binary search: 得分13/15")]},
    {"type": "text", "text": "第二題考過 Linked list。",
     "citations": [_cite("https://www.dcard.tw/f/tech_job/p/999", "憑空捏造的網址")]},
    {"type": "text", "text": "也有人說考 Tree。",
     "citations": [_cite("https://example.com/blog", "不在白名單")]},
    {"type": "text", "text": "沒有引用的一句話。"},
]


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _memory_ledger():
    llm_client.use_ledger(llm_client.MemoryLedger())


def test_only_citations_backed_by_this_searchs_results_are_kept() -> None:
    findings, queries, urls = community.parse_response(GOOD_CONTENT)
    assert queries == ["台積電 IT HackerRank 心得"]
    assert [f.claim for f in findings] == ["三題難度是 Easy、Medium、Medium。", "其中一題是 two pointer + binary search。"]
    # 捏造的網址（不在搜尋結果裡）與白名單外的網域都被丟掉
    assert all(s.url == DCARD for f in findings for s in f.sources)


def test_report_groups_sources_by_url() -> None:
    findings, _q, _u = community.parse_response(GOOD_CONTENT)
    grouped = community.CommunityReport("ok", findings=findings).by_url()
    title, texts, claims = grouped[DCARD]
    assert texts == ["考試難度我覺得是EMM", "(M) two pointer + binary search: 得分13/15"]
    assert len(claims) == 2


def test_request_is_restricted_to_community_sites() -> None:
    fake = FakeClient([_response(GOOD_CONTENT, searches=3)])
    report = community.search_community("需要 Python", user_id=1, hint="某公司 IT", client=fake)
    assert report.status == "ok" and report.searches == 3
    call = fake.calls[0]
    tool = call["tools"][0]
    assert tool["type"].startswith("web_search_") and tool["max_uses"] == community.MAX_SEARCHES
    assert set(tool["allowed_domains"]) == set(community.COMMUNITY_DOMAINS)
    assert "ptt.cc" in tool["allowed_domains"] and "dcard.tw" in tool["allowed_domains"]
    assert call["model"] == community.COMMUNITY_MODEL
    assert "某公司 IT" in call["messages"][0]["content"]


def test_paused_turn_is_continued_with_the_paused_content() -> None:
    first = [{"type": "server_tool_use", "name": "web_search", "input": {"query": "q1"}}]
    fake = FakeClient([_response(first, stop_reason="pause_turn"), _response(GOOD_CONTENT)])
    report = community.search_community("JD", user_id=1, client=fake)
    assert report.status == "ok" and len(fake.calls) == 2
    second_messages = fake.calls[1]["messages"]
    assert second_messages[-1] == {"role": "assistant", "content": first}, "暫停前的內容要原封不動送回去"
    assert report.queries[0] == "q1"


def test_daily_quota() -> None:
    for _ in range(community.COMMUNITY_DAILY_QUOTA):
        assert community.search_community("JD", user_id=7, client=FakeClient([_response(GOOD_CONTENT)])).status == "ok"
    over = community.search_community("JD", user_id=7, client=FakeClient([_response(GOOD_CONTENT)]))
    assert over.status == "quota"


def test_failure_does_not_use_up_the_quota() -> None:
    for _ in range(community.COMMUNITY_DAILY_QUOTA + 2):
        report = community.search_community("JD", user_id=8, client=FakeClient([RuntimeError("網路斷了")]))
        assert report.status == "error"
    assert community.search_community("JD", user_id=8, client=FakeClient([_response(GOOD_CONTENT)])).status == "ok"


def test_nothing_citable_is_reported_as_empty() -> None:
    report = community.search_community("JD", user_id=1, client=FakeClient([_response([GOOD_CONTENT[-1]])]))
    assert report.status == "empty" and not report.findings


def test_without_a_key_it_says_why_instead_of_calling(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    report = community.search_community("JD", user_id=1)
    assert report.status == "unavailable" and "ANTHROPIC_API_KEY" in report.message


def test_findings_are_saved_as_posts_with_original_text_and_summary_apart(db_session) -> None:
    db_session.add(User(id=1, email="u1@example.com", display_name="測試"))
    repo.save_jd(db_session, 1, "後端 A", "需要 Python")
    findings, _q, _u = community.parse_response(GOOD_CONTENT)
    report = community.CommunityReport("ok", findings=findings)

    assert repo.save_community_findings(db_session, 1, report) == (1, 0)
    post = next(j for j in repo.load_jds(db_session, 1) if j.kind == repo.KIND_POST)
    assert post.origin == "search" and post.source_url == DCARD
    assert "two pointer + binary search" in post.body and "Easy、Medium" not in post.body, "原文欄只放引用的原文"
    assert "Easy、Medium、Medium" in post.summary
    assert repo.load_jd_stats(db_session, 1).total == 1, "搜尋到的面經不進排序"
    assert "search" in repo.load_jd_citations(db_session, 1, kind=repo.KIND_POST)

    assert repo.save_community_findings(db_session, 1, report) == (0, 1), "同一個網址不存第二次"


def test_page_explains_when_search_is_unavailable(seeded_app_db, monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    page = str(Path(__file__).resolve().parent.parent / "app" / "pages" / "5_職缺JD.py")
    app = AppTest.from_file(page, default_timeout=60).run()
    app.text_input[0].set_value("UI 搜尋測試")
    app.text_area[0].set_value("需要 Python")
    app.button[0].click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    search = [b for b in app.button if "搜尋社群面經" in b.label]
    assert search and search[0].disabled
    assert any("ANTHROPIC_API_KEY" in i.value for i in app.info)
    for button in [b for b in app.button if b.label == "刪掉這份"]:
        button.click().run()
        app.run()
