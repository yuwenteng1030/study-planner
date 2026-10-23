"""鼓勵訊息：範本照情境挑、LLM 失敗時退回範本、每日額度。

測試**一律不呼叫外部服務**：用假的 ``anthropic.Anthropic`` 取代真的，
只檢查送出去的參數與各種回應的處理。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import llm.client as client_mod
from core.encouragement import Cheer, template_message


def _cheer(**kw) -> Cheer:
    base = dict(title="Two Sum", topic_name="Arrays", difficulty="Easy")
    base.update(kw)
    return Cheer(**base)


# ---------------------------------------------------------------------------
# 範本
# ---------------------------------------------------------------------------


def test_finishing_the_day_wins_over_everything_else() -> None:
    text = template_message(_cheer(difficulty="Hard", today_done=3, today_total=3))
    assert "3 題全部做完" in text and "「Two Sum」" in text


def test_hard_problem_gets_its_own_message() -> None:
    assert "Hard" in template_message(_cheer(difficulty="Hard", today_done=1, today_total=3))


def test_faster_than_estimate_says_by_how_much() -> None:
    text = template_message(_cheer(est_minutes=20, actual_minutes=12))
    assert "快了 8 分鐘" in text


def test_much_slower_than_estimate_is_reassuring_not_scolding() -> None:
    text = template_message(_cheer(est_minutes=20, actual_minutes=31))
    assert "花得比預估久" in text
    # 只慢一點點不特別說
    assert "花得比預估久" not in template_message(_cheer(est_minutes=20, actual_minutes=25))


def test_plain_completion_mentions_what_is_left_today() -> None:
    assert "還剩 2 題" in template_message(_cheer(today_done=1, today_total=3))
    # 不在計畫裡（從地圖做的）：沒有「今天還剩」可說，也不能炸
    assert "繼續保持" in template_message(_cheer())


def test_zero_planned_today_is_not_all_done() -> None:
    """今天沒排題目時 0/0 不能說成「全部做完」。"""
    assert "全部做完" not in template_message(_cheer(today_done=0, today_total=0))


# ---------------------------------------------------------------------------
# LLM：只有成功時才用它的字，其餘一律退回範本
# ---------------------------------------------------------------------------


class _FakeAnthropic:
    """記下送出去的參數，回傳預先設好的回應或拋出預先設好的例外。"""

    calls: list[dict] = []
    reply: object = None

    def __init__(self, *args, **kwargs) -> None:
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        _FakeAnthropic.calls.append(kwargs)
        if isinstance(_FakeAnthropic.reply, Exception):
            raise _FakeAnthropic.reply
        return _FakeAnthropic.reply


def _reply(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
    )


@pytest.fixture()
def fake_api(monkeypatch):
    import anthropic

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    monkeypatch.setattr(client_mod, "_calls", {})
    monkeypatch.setattr(client_mod, "_cheered", set())
    _FakeAnthropic.calls = []
    _FakeAnthropic.reply = _reply("Arrays 的雙指標練熟了，下一題會更快。")
    return _FakeAnthropic


def test_without_an_api_key_the_template_is_used(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50)
    assert (result.text, result.source) == ("範本句", "static")


def test_generated_text_is_used_and_marked(fake_api) -> None:
    result = client_mod.encourage(
        _cheer(est_minutes=20, actual_minutes=15, today_done=1, today_total=3), "範本句", user_id=1, quota=50
    )
    assert result.is_generated and result.text.startswith("Arrays")

    [sent] = fake_api.calls
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"] == {"effort": "low"}
    assert sent["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in sent["betas"]
    assert "thinking" not in sent  # 這個模型的思考不能關，送 disabled 會 400
    prompt = sent["messages"][0]["content"]
    assert "Two Sum" in prompt and "實際 15 分鐘" in prompt and "1 / 3" in prompt


@pytest.mark.parametrize(
    "reply",
    [
        _reply("", "end_turn"),  # 空字串
        _reply("被拒答時的殘字", "refusal"),
        _reply("寫到一半被截斷", "max_tokens"),
        RuntimeError("網路斷了"),
    ],
)
def test_anything_but_a_clean_answer_falls_back_to_the_template(fake_api, reply) -> None:
    fake_api.reply = reply
    result = client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50)
    assert (result.text, result.source) == ("範本句", "static")


def test_daily_quota_stops_calling_the_api(fake_api) -> None:
    for title in ("A", "B"):
        assert client_mod.encourage(_cheer(title=title), "範本句", user_id=7, quota=2).is_generated
    third = client_mod.encourage(_cheer(title="C"), "範本句", user_id=7, quota=2)
    assert third.source == "static"
    assert len(fake_api.calls) == 2
    # 別人的額度分開算
    assert client_mod.encourage(_cheer(title="C"), "範本句", user_id=8, quota=2).is_generated


def test_same_task_is_not_sent_twice_in_a_day(fake_api) -> None:
    """打勾、取消、再打勾：第二次用範本，不再花一次呼叫。"""
    assert client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50).is_generated
    assert client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50).source == "static"
    assert len(fake_api.calls) == 1


def test_a_failed_call_still_uses_up_quota(fake_api) -> None:
    """額度算的是「嘗試」：一直失敗也不能無限重打。"""
    fake_api.reply = RuntimeError("逾時")
    client_mod.encourage(_cheer(title="A"), "範本句", user_id=1, quota=1)
    fake_api.reply = _reply("這次會成功")
    assert client_mod.encourage(_cheer(title="B"), "範本句", user_id=1, quota=1).source == "static"
    assert len(fake_api.calls) == 1


def test_client_has_a_short_timeout_and_no_retries(fake_api, monkeypatch) -> None:
    seen = {}

    class _Spy(_FakeAnthropic):
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__()

    import anthropic

    monkeypatch.setattr(anthropic, "Anthropic", _Spy)
    client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50)
    assert seen == {"timeout": client_mod.ENCOURAGE_TIMEOUT_SECONDS, "max_retries": 0}


def test_generated_text_is_stripped_of_links_and_extra_lines(fake_api) -> None:
    fake_api.reply = _reply("做得好，看[這裡](https://evil.test/x) https://evil.test/y\n第二行")
    text = client_mod.encourage(_cheer(), "範本句", user_id=1, quota=50).text
    assert "http" not in text and "](" not in text and "第二行" not in text
    assert text.startswith("做得好，看這裡")


def test_toast_text_escapes_markdown() -> None:
    from core.encouragement import toast_text

    assert toast_text("**粗體** [x](y)") == r"\*\*粗體\*\* \[x\]\(y\)"
