"""額度存資料庫：重開伺服器不歸零、每天照使用者時區重算、退回沒用到的次數。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, event

import db.session as session_mod
from db import repositories as repo
from db.models import ApiUsage
from db.quota import DbLedger


@pytest.fixture
def ledger_db(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'quota.db'}", future=True)

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    from sqlalchemy.orm import sessionmaker

    monkeypatch.setattr(session_mod, "_engine", eng)
    monkeypatch.setattr(session_mod, "SessionLocal", sessionmaker(bind=eng, expire_on_commit=False, future=True))
    monkeypatch.setattr(session_mod, "_schema_ready", False)
    with session_mod.session_scope() as s:
        repo.ensure_user(s, 1)
    yield eng
    eng.dispose()


def _count(kind: str = "explain") -> int:
    with session_mod.session_scope() as s:
        rows = s.query(ApiUsage).filter_by(user_id=1, kind=kind).all()
        return sum(r.count for r in rows)


def test_reserve_caps_at_the_limit_and_persists(ledger_db) -> None:
    assert DbLedger().reserve(1, "explain", 4, limit=5) == 4
    # 「重開伺服器」：新的 ledger 物件，數字照樣在資料庫裡
    assert DbLedger().reserve(1, "explain", 4, limit=5) == 1
    assert DbLedger().reserve(1, "explain", 1, limit=5) == 0
    assert _count() == 5


def test_refund_gives_back_unused_calls(ledger_db) -> None:
    ledger = DbLedger()
    ledger.reserve(1, "explain", 5, limit=5)
    ledger.refund(1, "explain", 3)
    assert _count() == 2
    assert ledger.reserve(1, "explain", 5, limit=5) == 3


def test_kinds_are_counted_separately(ledger_db) -> None:
    ledger = DbLedger()
    ledger.reserve(1, "explain", 5, limit=5)
    assert ledger.reserve(1, "encourage", 1, limit=1) == 1


def test_explain_many_uses_the_injected_ledger(ledger_db, monkeypatch) -> None:
    import anthropic
    from types import SimpleNamespace

    import llm.client as client_mod

    class Down:
        def __init__(self, **_kw):
            self.messages = SimpleNamespace(create=self._create)

        def _create(self, **_kw):
            raise RuntimeError("429")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: Down(**kw))
    client_mod.use_ledger(DbLedger())
    tasks = [{"id": i, "title": f"T{i}", "static_note": "s"} for i in range(1, 4)]
    client_mod.explain_many(tasks, user_id=1, quota=10)
    # 全部失敗 → 全部退回，資料庫裡是 0
    assert _count() == 0


class _BrokenLedger:
    def reserve(self, *a, **k):
        raise RuntimeError("database is locked")

    def refund(self, *a, **k):
        raise RuntimeError("database is locked")


def test_a_broken_ledger_never_breaks_explanations_or_cheers(monkeypatch) -> None:
    """額度的存放處出錯（例如資料庫被鎖）時，退回預存文字，不拋例外。"""
    import anthropic
    from types import SimpleNamespace

    import llm.client as client_mod
    from core.encouragement import Cheer

    class Ok:
        def __init__(self, **_kw):
            self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
                content=[SimpleNamespace(type="text", text="生成")]))

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: Ok(**kw))
    monkeypatch.setattr(client_mod, "_cheered", set())
    client_mod.use_ledger(_BrokenLedger())

    out = client_mod.explain_many([{"id": 1, "title": "T", "static_note": "預存"}], user_id=1, quota=5)
    assert out[1].text == "預存" and not out[1].is_generated

    cheer = Cheer(title="T", topic_name="A", difficulty="Easy")
    assert client_mod.encourage(cheer, "範本", user_id=1, quota=5).text == "範本"
    # 預留失敗不能把「今天鼓勵過」佔住，否則之後資料庫好了也不會再試
    assert client_mod._cheered == set()
