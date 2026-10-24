"""職缺 JD：貼上職缺描述，看它提到哪些主題、原文是哪一句。

存下來的 JD 會進評分公式的 ``jd_factor``：越多份 JD 提到的主題，排計畫時越前面
（最多 ×2，見 README 設計決策 2）。分析在 ``core/jd_parser.py``，用關鍵字比對，
引用的一定是原文——理由寫在那支檔案的開頭。
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from api.deps import get_current_user, show_demo_notice  # noqa: E402
from core.jd_parser import MAX_JD_CHARS, Mention, analyze, md_escape  # noqa: E402
from core.jd_parser import highlight_markdown as highlighted  # noqa: E402
from core.scoring import jd_factor  # noqa: E402
from db import repositories as repo  # noqa: E402
from db.session import session_scope  # noqa: E402

st.set_page_config(page_title="職缺 JD", page_icon="📄", layout="centered")

#: 每個主題最多列幾句出處。
MAX_QUOTES = 3

def main() -> None:
    show_demo_notice()
    st.title("📄 職缺 JD")
    st.caption(
        "貼上想投的職缺描述，這裡會標出它提到哪些主題、引用的是哪一句原文。"
        "存下來的 JD 會影響排計畫的順序：越多份 JD 提到的主題排越前面（最多 ×2）。"
        "比對用關鍵字，只認明確的詞——寧可漏抓，也不要亂加分。"
    )
    user_id = get_current_user()

    _show_form(user_id)

    with session_scope() as session:
        analyses = repo.load_jd_analyses(session, user_id)
        topic_names = {t.id: t.name for t in repo.load_topic_nodes(session)}
        stats = repo.load_jd_stats(session, user_id)
        rows = [(jd.id, jd.title, jd.body, jd.created_at, a) for jd, a in analyses]

    if not rows:
        st.info("還沒有 JD。貼一份進來看看：IT 與其他職類都貼，哪些主題兩邊都要，就是最該先練的。")
        return

    st.divider()
    _show_summary(rows, stats, topic_names)
    st.divider()
    _show_saved(user_id, rows, topic_names)


_SAVED_KEY = "jd_saved_message"
_FORM_ROUND_KEY = "jd_form_round"


def _show_form(user_id: int) -> None:
    # 不用 clear_on_submit：存檔失敗（例如已存滿）時表單也會被清空，貼了兩萬字的人會很想哭。
    # 存成功才清空。做法是換一組新的 widget key（第幾輪就用第幾號）：只把舊 key 從
    # session_state 刪掉沒有用——瀏覽器那邊的輸入框還留著上一份的文字，
    # 實測存完第二份 JD 時，畫面上還是第一份的內容（AppTest 測不出來）。
    message = st.session_state.pop(_SAVED_KEY, None)
    if message is not None:
        _show_saved_message(message)
    form_round = st.session_state.get(_FORM_ROUND_KEY, 0)
    with st.form(f"jd_form_{form_round}"):
        title = st.text_input(
            "幫這份 JD 取個名字", max_chars=120, placeholder="例如：後端工程師 A", key=f"jd_title_{form_round}"
        )
        body = st.text_area(
            "JD 內容", height=220, max_chars=MAX_JD_CHARS, placeholder="把職缺頁面上的工作內容與條件整段貼進來",
            key=f"jd_body_{form_round}",
        )
        submitted = st.form_submit_button("分析並儲存", type="primary")
    if not submitted:
        return
    try:
        with session_scope() as session:
            jd = repo.save_jd(session, user_id, title, body)
            found = len(analyze(jd.body).topics)
            saved_title = jd.title
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state[_SAVED_KEY] = (saved_title, found)
    st.session_state[_FORM_ROUND_KEY] = form_round + 1
    st.session_state.pop(f"jd_title_{form_round}", None)  # 舊那一輪的值用不到了，不要一直留著
    st.session_state.pop(f"jd_body_{form_round}", None)
    st.rerun()


def _show_saved_message(message) -> None:
    saved_title, found = message
    if found:
        st.success(f"已存「{md_escape(saved_title)}」：提到題庫裡的 {found} 個主題。下次排計畫就會參考它。")
    else:
        st.warning(f"已存「{md_escape(saved_title)}」，但沒有比對到題庫裡的主題（可能都寫泛稱，例如「熟悉演算法」）。")


def _show_summary(rows, stats, topic_names: dict[str, str]) -> None:
    total = len(rows)
    st.subheader(f"這 {total} 份 JD 想要什麼")
    by_topic: dict[str, list[tuple[str, Mention]]] = {}
    for _id, title, _body, _at, analysis in rows:
        for topic_id, mention in analysis.topics.items():
            by_topic.setdefault(topic_id, []).append((title, mention))

    if not by_topic:
        st.info("還沒有比對到任何主題，排序跟沒貼 JD 一樣。")
    for topic_id, cites in sorted(by_topic.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        factor = jd_factor(topic_id, stats)
        st.markdown(
            f"**{md_escape(topic_names.get(topic_id, topic_id))}**　"
            f"{len(cites)}/{total} 份提到　·　排序權重 ×{factor:.2f}"
        )
        for title, mention in cites[:MAX_QUOTES]:
            st.markdown(f"> {highlighted(mention)}  \n> — {md_escape(title)}")
        if len(cites) > MAX_QUOTES:
            st.caption(f"另外 {len(cites) - MAX_QUOTES} 份也提到。")

    skills: dict[str, int] = {}
    generic = 0
    for *_rest, analysis in rows:
        for label in analysis.other_skills:
            skills[label] = skills.get(label, 0) + 1
        generic += bool(analysis.generic)
    if skills:
        st.markdown("**題庫以外的技能**（刷題練不到，要另外準備）")
        st.write("　".join(f"{label} {n}/{total}" for label, n in sorted(skills.items(), key=lambda kv: -kv[1])))
    if generic:
        st.caption(
            f"有 {generic} 份 JD 寫了「資料結構」「演算法」這類泛稱：每個主題都沾得上邊，"
            "所以不替任何一個主題加分。"
        )


def _show_saved(user_id: int, rows, topic_names: dict[str, str]) -> None:
    st.subheader("已存的 JD")
    for jd_id, title, body, created_at, analysis in rows:
        names = "、".join(topic_names.get(t, t) for t in analysis.topics) or "沒有比對到主題"
        with st.expander(f"{md_escape(title)}（{created_at:%m/%d}）"):
            st.caption(f"提到：{names}")
            st.text(body if len(body) <= 3000 else body[:3000] + "\n…")
            if st.button("刪掉這份 JD", key=f"del-jd-{jd_id}"):
                with session_scope() as session:
                    repo.delete_jd(session, user_id, jd_id)
                st.rerun()


main()
