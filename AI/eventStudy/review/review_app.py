"""
审核界面（Streamlit，方案 3.2）

两个 Tab：
1. 待审核事件：展示 Redis 待审草稿，人工确认事件类型/子类型/条件/
   重要性/预期值/实际值/前值，通过或忽略
2. 影响结果确认：展示 Redis 影响草稿（各资产 × 窗口 CAR/方向/污染），
   勾选资产后确认落表 PG event_impacts

运行：streamlit run AI/eventStudy/review/review_app.py
"""

import streamlit as st

from AI.eventStudy.collectors.config import get_redis_client, is_redis_available
from AI.eventStudy.db.connection import get_connection
from AI.eventStudy.review import review_dao

st.set_page_config(page_title="事件研究 — 审核", layout="wide")


def get_conn():
    """会话级 PG 连接；失效（空闲断开/重启）时自动重建。"""
    conn = st.session_state.get("pg_conn")
    try:
        if conn is None or conn.closed:
            raise ConnectionError("连接已关闭")
        conn.execute("SELECT 1")
        return conn
    except Exception:
        try:
            conn = get_connection()
        except Exception:
            return None
        st.session_state["pg_conn"] = conn
        return conn


def _status_line() -> None:
    redis_ok = is_redis_available()
    pg_ok = get_conn() is not None
    if pg_ok and redis_ok:
        st.success("PostgreSQL / Redis 可用")
    else:
        if not pg_ok:
            st.error("PostgreSQL 不可用（请启动 docker-compose 的 postgres 服务）")
        if not redis_ok:
            st.error("Redis 不可用 — 待审草稿与影响草稿无法读写（请启动 docker-compose 的 redis 服务）")


def render_pending_tab():
    st.subheader("待审核事件草稿（Redis events:pending:*）")
    events = review_dao.get_pending_events()
    if not events:
        st.info("暂无待审核事件。每日定时任务采集后自动进入队列。")
        return

    options = {f"#{e['draft_id']} [{e.get('announced_at', '')[:16]}] {e['title'][:60]}": e
               for e in events}
    selected = st.selectbox("选择事件", list(options.keys()))
    draft = options[selected]
    st.markdown(f"**标题**: {draft['title']}")
    st.markdown(f"**内容**: {draft.get('content') or '（无）'}")
    st.markdown(f"**来源**: {draft.get('source') or '未知'}　**时间**: {draft.get('announced_at')}")
    st.markdown(f"**原文链接**: {draft.get('source_url') or '（无）'}")
    st.markdown(f"**爬虫重要性提示**: {draft.get('importance_hint') or '（无，请人工填写）'}")

    with st.form(f"review_form_{draft['draft_id']}"):
        col1, col2 = st.columns(2)
        with col1:
            event_type = st.text_input("事件类型", value=draft.get("event_type", ""),
                                       placeholder="宏观数据 / 央行 / 地缘 / 产业政策…")
            event_subtype = st.text_input("事件子类型", value=draft.get("event_subtype", ""),
                                          placeholder="CPI / LPR / 降准 / 加息…")
            event_condition = st.selectbox(
                "关键条件", ["", "超预期", "符合预期", "低于预期", "利好", "利空", "中性"],
                index=0,
            )
        with col2:
            importance = st.slider("重要性", 1, 5,
                                   value=int(draft.get("importance_hint") or 3))
            # number_input 未填写时返回 0.0，需用勾选区分"未填写"与"合法 0 值"
            fill_expected = st.checkbox("填写预期值", value=False)
            expected_value = st.number_input("预期值", value=0.0, format="%.4f",
                                             disabled=not fill_expected)
            fill_actual = st.checkbox("填写实际值", value=False)
            actual_value = st.number_input("实际值", value=0.0, format="%.4f",
                                           disabled=not fill_actual)
            fill_previous = st.checkbox("填写前值", value=False)
            previous_value = st.number_input("前值", value=0.0, format="%.4f",
                                             disabled=not fill_previous)
        operator = st.text_input("操作者", value="admin")
        col_a, col_b = st.columns(2)
        approve = col_a.form_submit_button("✅ 审核通过", type="primary")
        ignore = col_b.form_submit_button("⏭️ 忽略")
        confirm = st.checkbox("二次确认（防止误操作）", value=False)

    if approve and confirm:
        conn = get_conn()
        if conn is None:
            st.error("PostgreSQL 不可用，无法审核")
        else:
            try:
                event_id = review_dao.approve_event(conn, draft["draft_id"], {
                    "event_type": event_type or None,
                    "event_subtype": event_subtype or None,
                    "event_condition": event_condition or None,
                    "importance": importance,
                    # 未勾选 → None；勾选后输入 0 是合法值（M4）
                    "expected_value": expected_value if fill_expected else None,
                    "actual_value": actual_value if fill_actual else None,
                    "previous_value": previous_value if fill_previous else None,
                }, operator=operator)
                st.success(f"已通过并写入事件库（event_id={event_id}），待影响计算")
                st.rerun()
            except Exception as e:
                st.error(f"审核通过失败: {e}")
    elif approve and not confirm:
        st.warning("请勾选二次确认")
    if ignore and confirm:
        conn = get_conn()
        if conn is None:
            st.error("PostgreSQL 不可用，无法审核")
        else:
            try:
                event_id = review_dao.ignore_event(conn, draft["draft_id"], operator=operator)
                st.success(f"已忽略（event_id={event_id}，保留用于爬虫去重）")
                st.rerun()
            except Exception as e:
                st.error(f"忽略失败: {e}")


def render_impacts_tab():
    st.subheader("影响结果确认（Redis event_impacts:draft:*）")
    conn = get_conn()
    if conn is None:
        st.error("PostgreSQL 不可用，无法确认落表")
        return
    drafts = review_dao.get_impact_drafts()
    if not drafts:
        st.info("暂无待确认的影响结果。定时任务对已通过事件计算后生成。")
        return

    options = {
        f"#{d['event_id']} {review_dao.get_event_title(conn, d['event_id'])[:50]} (t0={d.get('t0', '?')})": d
        for d in drafts
    }
    selected = st.selectbox("选择事件", list(options.keys()))
    draft = options[selected]
    event_id = draft["event_id"]
    st.markdown(f"**t0**: {draft.get('t0')}　**计算时间**: {draft.get('computed_at', '')[:19]}")

    # 展示各资产 × 窗口结果
    assets = draft.get("assets", {})
    header = ["资产", "窗口", "CAR", "t值", "方向", "污染", "备注"]
    rows = []
    for ticker, per_window in assets.items():
        for wt in ("pre_event_5d", "event_day", "post_event_5d"):
            r = per_window.get(wt, {})
            direction = {1: "利好 ↑", -1: "利空 ↓", 0: "中性 →"}.get(r.get("direction"), "?")
            rows.append([
                ticker, wt,
                f"{r['cumulative_abnormal_return'] * 100:.3f}%" if r.get("cumulative_abnormal_return") is not None else "—",
                f"{r['t_stat']:.2f}" if r.get("t_stat") is not None else "—",
                direction,
                "⚠️ 是" if r.get("is_contaminated") else "否",
                r.get("error") or "",
            ])
    st.table(rows)

    with st.form(f"impact_form_{event_id}"):
        cols = st.columns(len(assets))
        checks = {}
        for i, ticker in enumerate(assets.keys()):
            checks[ticker] = cols[i].checkbox(ticker, value=False)
        operator = st.text_input("操作者", value="admin", key=f"op_{event_id}")
        confirm = st.checkbox("二次确认（仅勾选的资产落表，未勾选不落表）", value=False)
        submit = st.form_submit_button("✅ 确认落表", type="primary")

    if submit and confirm:
        selected_tickers = [t for t, v in checks.items() if v]
        if not selected_tickers:
            st.warning("请至少勾选一个资产")
        else:
            try:
                count = review_dao.confirm_impacts(conn, event_id, selected_tickers, operator=operator)
                st.success(f"已落表 {count} 条影响记录（仅勾选资产）")
                st.rerun()
            except Exception as e:
                st.error(f"确认失败: {e}")


def main():
    st.title("事件研究系统 — 人工审核")
    _status_line()
    tab_pending, tab_impacts = st.tabs(["待审核事件", "影响结果确认"])
    with tab_pending:
        render_pending_tab()
    with tab_impacts:
        render_impacts_tab()


if __name__ == "__main__":
    main()
