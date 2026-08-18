"""
审核界面（Streamlit，方案 3.2 + 2026-08-19 批量表格化改版）

两个 Tab：
1. 待审核事件：全量表格展示（可编辑列 = 类型/子类型/条件/重要性/操作），
   批量提交（通过/忽略/跳过）；AI 预填建议作为各列默认值
2. 影响结果确认：全部草稿平铺为 资产 × 窗口 表格，勾选后批量落表

运行：streamlit run AI/eventStudy/review/review_app.py
"""

import pandas as pd
import streamlit as st

from AI.eventStudy.collectors.config import WINDOW_TYPES, is_redis_available
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


CONDITION_OPTIONS = ["", "超预期", "符合预期", "低于预期", "利好", "利空", "中性"]
ACTION_OPTIONS = ["跳过", "通过", "忽略"]


# ==================== Tab 1: 待审核事件（批量表格） ====================

def render_pending_tab():
    st.subheader("待审核事件（批量）")
    events = review_dao.get_pending_events()
    if not events:
        st.info("暂无待审核事件。每日定时任务采集后自动进入队列。")
        return

    # AI 预填按钮
    from AI.eventStudy.review import ai_prelabel
    if st.button("🤖 AI 预填全部待审事件", disabled=not is_redis_available()):
        with st.spinner("AI 预填中…"):
            n = ai_prelabel.prelabel_events(review_dao.get_pending_events())
        st.success(f"已为 {n} 条事件生成预填建议")
        st.rerun()

    # 组装表格：AI 建议作为各列默认值
    rows = []
    for e in events:
        s = e.get("ai_suggestions") or {}
        rows.append({
            "draft_id": e["draft_id"],
            "时间": str(e.get("announced_at", ""))[:16],
            "来源": e.get("source", ""),
            "标题": e["title"][:60],
            "事件类型": s.get("event_type") or "",
            "事件子类型": s.get("event_subtype") or "",
            "关键条件": s.get("event_condition") or "",
            "重要性": int(s.get("importance") or e.get("importance_hint") or 3),
            "操作": "跳过",
        })
    df = pd.DataFrame(rows)

    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "draft_id": st.column_config.NumberColumn("#", disabled=True),
            "时间": st.column_config.TextColumn(disabled=True),
            "来源": st.column_config.TextColumn(disabled=True),
            "标题": st.column_config.TextColumn(disabled=True, width="large"),
            "事件类型": st.column_config.TextColumn(required=False,
                                                    help="宏观数据 / 央行 / 地缘 / 产业政策…"),
            "事件子类型": st.column_config.TextColumn(required=False,
                                                      help="CPI / LPR / 降准…"),
            "关键条件": st.column_config.SelectboxColumn(options=CONDITION_OPTIONS),
            "重要性": st.column_config.NumberColumn(min_value=1, max_value=5, step=1),
            "操作": st.column_config.SelectboxColumn(options=ACTION_OPTIONS,
                                                     help="跳过 = 本次不处理"),
        },
        key="pending_editor",
    )

    st.caption("预期值/实际值/前值：批量审核采用 AI 提取值（如有，见详情）；"
               "需人工改数值的事件可单独在 Adminer 中修正。")

    # 查看单条原文
    with st.expander("查看事件原文"):
        detail_map = {f"#{e['draft_id']} {e['title'][:40]}": e for e in events}
        detail = st.selectbox("选择事件", list(detail_map.keys()))
        if detail:
            d = detail_map[detail]
            st.markdown(f"**标题**: {d['title']}")
            st.markdown(f"**内容**: {d.get('content') or '（无）'}")
            st.markdown(f"**来源**: {d.get('source') or '未知'}　**时间**: {d.get('announced_at')}")
            st.markdown(f"**原文链接**: {d.get('source_url') or '（无）'}")
            if d.get("ai_suggestions"):
                st.json(d["ai_suggestions"])

    # 批量提交
    col_btn, col_confirm = st.columns([1, 2])
    submit = col_btn.button("🚀 批量提交", type="primary")
    confirm = col_confirm.checkbox("二次确认（仅处理「操作」列非「跳过」的行）", value=False)

    if submit and confirm:
        conn = get_conn()
        if conn is None:
            st.error("PostgreSQL 不可用，无法审核")
            return
        from AI.eventStudy.processing import event_study
        approved = ignored = computed = 0
        for _, row in edited.iterrows():
            action = row["操作"]
            if action == "跳过":
                continue
            fields = {
                "event_type": row["事件类型"] or None,
                "event_subtype": row["事件子类型"] or None,
                "event_condition": row["关键条件"] or None,
                "importance": int(row["重要性"]),
            }
            # 数值三列采用 AI 提取值
            suggestion = next(
                (e.get("ai_suggestions") or {}
                 for e in events if e["draft_id"] == row["draft_id"]), {})
            fields["expected_value"] = suggestion.get("expected_value")
            fields["actual_value"] = suggestion.get("actual_value")
            fields["previous_value"] = suggestion.get("previous_value")
            try:
                if action == "通过":
                    event_id = review_dao.approve_event(conn, int(row["draft_id"]), fields)
                    approved += 1
                    # 确认后即刻计算影响（t0 对齐 + 4 指数 × 3 窗口），
                    # 结果立即出现在「影响结果确认」Tab
                    try:
                        event_study.compute_all_windows(conn, event_id)
                        computed += 1
                    except Exception as e:
                        st.warning(f"事件 {event_id} 影响即时计算失败（批处理会兜底重算）: {e}")
                else:
                    review_dao.ignore_event(conn, int(row["draft_id"]))
                    ignored += 1
            except Exception as e:
                st.error(f"#{row['draft_id']} 处理失败: {e}")
        msg = f"批量提交完成：通过 {approved} 条，忽略 {ignored} 条"
        if computed:
            msg += f"；影响已即时计算 {computed} 条（切到「影响结果确认」查看）"
        st.success(msg)
        st.rerun()
    elif submit and not confirm:
        st.warning("请勾选二次确认")


# ==================== Tab 2: 影响结果确认（批量表格） ====================

def render_impacts_tab():
    st.subheader("影响结果确认（批量）")
    conn = get_conn()
    if conn is None:
        st.error("PostgreSQL 不可用，无法确认落表")
        return
    drafts = review_dao.get_impact_drafts()
    if not drafts:
        st.info("暂无待确认的影响结果。定时任务对已通过事件计算后生成。")
        return

    # 全部草稿平铺为 资产 × 窗口 表格
    rows = []
    for d in drafts:
        title = review_dao.get_event_title(conn, d["event_id"])
        for ticker, per_window in d.get("assets", {}).items():
            for wt in WINDOW_TYPES:
                r = per_window.get(wt, {})
                car = r.get("cumulative_abnormal_return")
                rows.append({
                    "event_id": d["event_id"],
                    "标题": title[:40],
                    "t0": d.get("t0", ""),
                    "资产": ticker,
                    "窗口": wt,
                    "CAR%": round(car * 100, 3) if car is not None else None,
                    "t值": r.get("t_stat"),
                    "方向": {1: "利好 ↑", -1: "利空 ↓", 0: "中性 →"}.get(r.get("direction"), "?"),
                    "污染": "⚠️" if r.get("is_contaminated") else "",
                    "备注": r.get("error") or "",
                    "勾选": False,
                })
    df = pd.DataFrame(rows)

    edited = st.data_editor(
        df,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "event_id": st.column_config.NumberColumn("#", disabled=True),
            "标题": st.column_config.TextColumn(disabled=True, width="large"),
            "t0": st.column_config.TextColumn(disabled=True),
            "资产": st.column_config.TextColumn(disabled=True),
            "窗口": st.column_config.TextColumn(disabled=True),
            "CAR%": st.column_config.NumberColumn(format="%.3f", disabled=True),
            "t值": st.column_config.NumberColumn(format="%.2f", disabled=True),
            "方向": st.column_config.TextColumn(disabled=True),
            "污染": st.column_config.TextColumn(disabled=True),
            "备注": st.column_config.TextColumn(disabled=True),
            "勾选": st.column_config.CheckboxColumn(help="勾选 = 落表正式记录"),
        },
        key="impacts_editor",
    )

    col_btn, col_confirm = st.columns([1, 2])
    submit = col_btn.button("🚀 确认落表", type="primary")
    confirm = col_confirm.checkbox("二次确认（仅勾选行落表，未勾选不落表）", value=False)

    if submit and confirm:
        # 按事件聚合勾选的资产
        checked = edited[edited["勾选"]]
        if checked.empty:
            st.warning("请至少勾选一行")
            return
        by_event = {}
        for _, row in checked.iterrows():
            by_event.setdefault(int(row["event_id"]), set()).add(row["资产"])
        total = 0
        for event_id, tickers in by_event.items():
            try:
                total += review_dao.confirm_impacts(conn, event_id, sorted(tickers))
            except Exception as e:
                st.error(f"事件 {event_id} 确认失败: {e}")
        st.success(f"已落表 {total} 条影响记录")
        st.rerun()


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
