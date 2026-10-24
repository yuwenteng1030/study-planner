"""職缺 JD 與社群面經：貼上職缺描述或考試心得，看它提到哪些主題、原文是哪一句。

存下來的 **JD** 會進評分公式的 ``jd_factor``：越多份 JD 提到的主題，排計畫時越前面
（最多 ×2，見 README 設計決策 2）。**社群面經**（PTT、Dcard、Threads 上的考試心得）只列出來
給人看、不進排序（設計決策 16）。分析在 ``core/jd_parser.py``，用關鍵字比對，
引用的一定是原文——理由寫在那支檔案的開頭。

不自動去抓社群網站：多數社群禁止爬取（Dcard 會擋機器人、IG／Threads 要登入），
而且使用者自己讀過、覺得可信的那幾篇，比爬來的一大堆更有參考價值。
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
from db.quota import DbLedger  # noqa: E402
from db.session import session_scope  # noqa: E402
from llm import client as llm_client  # noqa: E402
from llm import community  # noqa: E402

# 社群搜尋的每日次數記在資料庫（重開伺服器不歸零）
llm_client.use_ledger(DbLedger())

st.set_page_config(page_title="職缺與面經", page_icon="📄", layout="centered")

_KIND_LABELS = {repo.KIND_JD: "📄 職缺描述（JD）", repo.KIND_POST: "💬 社群面經（PTT／Dcard／Threads…）"}

#: 每個主題最多列幾句出處。
MAX_QUOTES = 3

def main() -> None:
    show_demo_notice()
    st.title("📄 職缺與面經")
    st.caption(
        "貼上想投的職缺描述，或在 PTT、Dcard、Threads 讀到的考試心得，這裡會標出它們提到哪些主題、"
        "引用的是哪一句原文。**JD** 會影響排計畫的順序（越多份提到的主題越前面，最多 ×2）；"
        "**面經**只列出來參考、不影響排序。比對用關鍵字，只認明確的詞——寧可漏抓，也不要亂加分。"
    )
    user_id = get_current_user()

    _show_form(user_id)
    _show_community_search(user_id)

    with session_scope() as session:
        analyses = repo.load_jd_analyses(session, user_id)
        topic_names = {t.id: t.name for t in repo.load_topic_nodes(session)}
        stats = repo.load_jd_stats(session, user_id)
        rows = [(jd.id, jd.kind, jd.title, jd.source_url, jd.body, jd.created_at, a) for jd, a in analyses]
        summaries = {jd.id: (jd.origin, jd.summary) for jd, _a in analyses}

    if not rows:
        st.info(
            "還沒有資料。貼一份 JD 進來看看：IT 與其他職類都貼，哪些主題兩邊都要，就是最該先練的。"
            "讀到考試心得也貼進來，看看實際考過哪些題型。"
        )
        return

    jds = [r for r in rows if r[1] == repo.KIND_JD]
    posts = [r for r in rows if r[1] == repo.KIND_POST]
    st.divider()
    _show_topics(jds, f"這 {len(jds)} 份 JD 想要什麼", topic_names, stats=stats)
    _show_other_skills(jds)
    st.divider()
    _show_topics(posts, f"這 {len(posts)} 篇面經說考了什麼", topic_names, stats=None)
    st.divider()
    _show_saved(user_id, rows, topic_names, summaries)


def _show_community_search(user_id: int) -> None:
    """從存好的 JD 自動去社群找面經（Claude 網路搜尋），找到的存成面經。"""
    with session_scope() as session:
        jds = [(jd.id, jd.title, jd.body) for jd in repo.load_jds(session, user_id) if jd.kind == repo.KIND_JD]
    if not jds:
        return
    st.subheader("🔎 從 JD 找社群面經")
    st.caption(
        f"Claude 會依 JD 判斷公司與職缺，到 {'、'.join(community.COMMUNITY_DOMAINS)} 搜尋考試與面試心得，"
        "整理出考了哪些題型，每一句都附原文出處。找到的會存成面經（只參考、不進排序）。"
        f"每次最多搜尋 {community.MAX_SEARCHES} 次、約需半分鐘；每天最多 {community.COMMUNITY_DAILY_QUOTA} 次。"
    )
    available = llm_client.is_available()
    if not available:
        st.info(
            "這個功能要用 Claude 搜尋網路：本機要設定 `ANTHROPIC_API_KEY`；公開展示版不開放"
            "（避免陌生人花掉部署者的錢）。沒有 key 也可以自己把讀到的心得貼進上面的表單。",
            icon="🔑",
        )
    by_id = {jd_id: (title, body) for jd_id, title, body in jds}
    choice = st.selectbox("用哪一份 JD 找", list(by_id), format_func=lambda i: by_id[i][0], key="community_jd")
    hint = st.text_input(
        "補充公司或職缺（選填，JD 裡沒寫公司名稱時很有用）", max_chars=200, key="community_hint",
        placeholder="例如：某半導體公司 IT 應用系統工程師",
    )
    if not st.button("🔎 搜尋社群面經", disabled=not available, type="primary"):
        return
    title, body = by_id[choice]
    # 在交易外呼叫：搜尋要等半分鐘以上，不能抓著資料庫的寫入鎖
    with st.spinner("Claude 正在搜尋社群上的面試心得……"):
        report = community.search_community(body, user_id=user_id, hint=hint)
    if report.status != "ok":
        (st.warning if report.status in ("empty", "quota") else st.error)(report.message)
        if report.queries:
            st.caption("搜尋過：" + "、".join(md_escape(q) for q in report.queries))
        return
    with session_scope() as session:
        saved, skipped = repo.save_community_findings(session, user_id, report)
    st.success(
        f"從「{md_escape(title)}」找到 {len(report.by_url())} 篇相關心得，新存了 {saved} 篇"
        + (f"（{skipped} 篇之前存過或已存滿）" if skipped else "")
        + "。下面「面經說考了什麼」已經更新。"
    )
    with st.expander("Claude 的整理（每句都附原文出處）", expanded=True):
        for finding in report.findings:
            links = "、".join(_attribution(src.title, src.url) for src in finding.sources)
            st.markdown(f"- {md_escape(finding.claim)}　— {links}")
        if report.queries:
            st.caption("搜尋過：" + "、".join(md_escape(q) for q in report.queries))
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
        kind = st.radio(
            "這是", list(_KIND_LABELS), format_func=_KIND_LABELS.get, horizontal=True, key=f"jd_kind_{form_round}"
        )
        title = st.text_input(
            "取個名字", max_chars=120, placeholder="例如：後端工程師 A、Dcard 2026/06 IT 心得", key=f"jd_title_{form_round}"
        )
        source_url = st.text_input(
            "原文網址（選填）", max_chars=500, placeholder="https://…", key=f"jd_url_{form_round}"
        )
        body = st.text_area(
            "內容", height=220, max_chars=MAX_JD_CHARS,
            placeholder="職缺頁面的工作內容與條件，或心得文裡講考試的那幾段，整段貼進來",
            key=f"jd_body_{form_round}",
        )
        submitted = st.form_submit_button("分析並儲存", type="primary")
    if not submitted:
        return
    try:
        with session_scope() as session:
            jd = repo.save_jd(session, user_id, title, body, kind=kind, source_url=source_url)
            found = len(analyze(jd.body).topics)
            saved_title = jd.title
    except ValueError as exc:
        st.error(str(exc))
        return
    st.session_state[_SAVED_KEY] = (saved_title, found, kind)
    st.session_state[_FORM_ROUND_KEY] = form_round + 1
    for name in ("kind", "title", "url", "body"):  # 舊那一輪的值用不到了，不要一直留著
        st.session_state.pop(f"jd_{name}_{form_round}", None)
    st.rerun()


def _show_saved_message(message) -> None:
    saved_title, found, kind = message
    effect = "下次排計畫就會參考它。" if kind == repo.KIND_JD else "面經只列出來參考，不影響排序。"
    if found:
        st.success(f"已存「{md_escape(saved_title)}」：提到題庫裡的 {found} 個主題。{effect}")
    else:
        st.warning(f"已存「{md_escape(saved_title)}」，但沒有比對到題庫裡的主題（可能都寫泛稱，例如「熟悉演算法」）。")


def _attribution(title: str, url: str | None) -> str:
    """出處那一行：有網址就做成連結。網址存檔前已經擋掉 http(s) 以外與會截斷語法的字元。"""
    return f"[{md_escape(title)}]({url})" if url else md_escape(title)


def _show_topics(rows, heading: str, topic_names: dict[str, str], *, stats) -> None:
    """主題 → 幾份提到＋原文引用。``stats`` 是 JD 的統計；面經傳 ``None``（不進排序，不顯示權重）。"""
    if not rows:
        return
    total = len(rows)
    st.subheader(heading)
    if stats is None:
        st.caption("面經是個人經驗、每一梯不同，只列出來參考，不影響排序。重點題組的取捨會參考這些。")
    by_topic: dict[str, list[tuple[str, str | None, Mention]]] = {}
    for _id, _kind, title, url, _body, _at, analysis in rows:
        for topic_id, mention in analysis.topics.items():
            by_topic.setdefault(topic_id, []).append((title, url, mention))

    if not by_topic:
        st.info("還沒有比對到任何主題。")
    for topic_id, cites in sorted(by_topic.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        weight = f"　·　排序權重 ×{jd_factor(topic_id, stats):.2f}" if stats is not None else ""
        st.markdown(
            f"**{md_escape(topic_names.get(topic_id, topic_id))}**　{len(cites)}/{total} 份提到{weight}"
        )
        for title, url, mention in cites[:MAX_QUOTES]:
            st.markdown(f"> {highlighted(mention)}  \n> — {_attribution(title, url)}")
        if len(cites) > MAX_QUOTES:
            st.caption(f"另外 {len(cites) - MAX_QUOTES} 份也提到。")


def _show_other_skills(rows) -> None:
    if not rows:
        return
    total = len(rows)
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


def _show_saved(user_id: int, rows, topic_names: dict[str, str], summaries) -> None:
    st.subheader("已存的 JD 與面經")
    for jd_id, kind, title, url, body, created_at, analysis in rows:
        names = "、".join(topic_names.get(t, t) for t in analysis.topics) or "沒有比對到主題"
        origin, summary = summaries.get(jd_id, ("user", None))
        icon = "📄" if kind == repo.KIND_JD else ("🔎" if origin == "search" else "💬")
        with st.expander(f"{icon} {md_escape(title)}（{created_at:%m/%d}）"):
            st.caption(f"提到：{names}")
            if summary:
                st.markdown("**✨ Claude 依原文整理**（不是原文）")
                for line in summary.splitlines():
                    st.markdown(f"- {md_escape(line)}")
                st.markdown("**引用的原文**")
            if url:
                st.markdown(f"原文：{_attribution(url, url)}")
            st.text(body if len(body) <= 3000 else body[:3000] + "\n…")
            if st.button("刪掉這份", key=f"del-jd-{jd_id}"):
                with session_scope() as session:
                    repo.delete_jd(session, user_id, jd_id)
                st.rerun()


main()
