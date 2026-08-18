"""
事件采集爬虫（方案 3.1）

从财经快讯源抓取可能影响市场的事件，写入 Redis 待审核草稿
（events:pending:{draft_id}）。抓取内容仅用于内部研究。

主源：财联社电报、金十数据快讯（自带重要性星级）
备源：新浪财经 7x24、东方财富快讯（默认关闭，env 开启）

爬虫不去重判断"是否影响市场"，全部进入待审队列由人工审核；
仅做简单标题去重（对 PG 已处理事件 + Redis 已有草稿）。
"""

import hashlib
import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta

import requests

from AI.eventStudy.collectors.config import (
    CRAWLER_CONFIG, PENDING_DRAFT_TTL,
    KEY_PENDING_EVENT, KEY_DRAFT_SEQ,
    get_redis_client, is_redis_available,
)

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))  # 北京时间

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.cls.cn/",
}

_SESSION = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = requests.Session()
        _SESSION.headers.update(_HEADERS)
    return _SESSION


def _iso_ts(text: str) -> str:
    """解析各源的时间字符串 → ISO8601（+08:00）。失败返回 None。"""
    if not text:
        return None
    text = str(text).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?", text):
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CST)
        return dt.astimezone(CST).isoformat()
    if re.match(r"^\d{10}$", text):  # unix 秒
        return datetime.fromtimestamp(int(text), tz=CST).isoformat()
    return None


def _normalize(title: str, content: str, url: str, announced_at: str,
               importance_hint=None, source: str = "") -> dict:
    """标准化事件结构（与方案 3.1.1 数据结构示例一致）。"""
    ts = _iso_ts(announced_at)
    if not ts:
        return None
    return {
        "title": (title or "").strip(),
        "content": (content or "").strip(),
        "source_url": url or "",
        "announced_at": ts,
        "importance_hint": importance_hint,  # 爬虫星级提示，仅供审核参考
        "source": source,
    }


# ==================== 各源解析器 ====================

def _make_cls_sign(params: dict) -> str:
    """财联社 v1 接口签名：参数按 key 排序拼接 → SHA-1 → MD5。"""
    sign_str = "&".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    sha1 = hashlib.sha1(sign_str.encode()).hexdigest()
    return hashlib.md5(sha1.encode()).hexdigest()


# 财联社分级 → importance_hint（A 加红 / B 重要 / C 普通；审核时可人工改）
_CLS_IMPORTANCE_BY_LEVEL = {"A": 5, "B": 4, "C": 3}


def _normalize_cls_item(item: dict):
    """财联社电报条目 → 标准化事件结构。"""
    level = item.get("level", "C")
    return _normalize(
        item.get("title") or (item.get("content") or "")[:50],
        item.get("content") or "",
        f"https://www.cls.cn/detail/{item.get('id', '')}",
        item.get("ctime"),
        importance_hint=_CLS_IMPORTANCE_BY_LEVEL.get(level),
        source="财联社电报",
    )


def _fetch_cls_telegraph():
    """财联社电报（主源，最快）。

    v1 接口（参数排序 → SHA-1 → MD5 签名，支持深度分页）；
    失败回退 nodeapi 接口（live 刷新用，无需签名）。
    """
    cfg = CRAWLER_CONFIG["cls_telegraph"]
    events = []
    # 主：v1 接口（带签名）
    try:
        params = {
            "app": "CailianpressWeb",
            "os": "web",
            "sv": cfg.get("sv", "8.4.6"),
            "refresh_type": "1",
            "rn": "30",
            "last_time": str(int(time.time())),
            "category": "",
        }
        params["sign"] = _make_cls_sign(params)
        resp = _session().get(cfg["url"], params=params, timeout=15)
        data = resp.json()
        if data.get("errno") in (None, 0, "0"):
            roll = (data.get("data") or {}).get("roll_data") or []
            for item in roll:
                ev = _normalize_cls_item(item)
                if ev and ev["title"]:
                    events.append(ev)
    except Exception as e:
        logger.warning(f"财联社 v1 接口抓取失败: {e}")
    if events:
        return events
    # 回退：nodeapi 接口（无需签名）
    try:
        resp = _session().get(
            cfg.get("nodeapi_url", ""),
            params={"app": "CailianpressWeb", "os": "web",
                    "sv": cfg.get("sv", "8.4.6"), "rn": "30"},
            timeout=15,
        )
        data = resp.json()
        if data.get("error") == 0:
            roll = (data.get("data") or {}).get("roll_data") or []
            for item in roll:
                ev = _normalize_cls_item(item)
                if ev and ev["title"]:
                    events.append(ev)
    except Exception as e:
        logger.warning(f"财联社 nodeapi 回退抓取失败: {e}")
    return events


def _fetch_jin10_flash():
    """金十数据快讯（主源，自带重要性星级）。"""
    url = CRAWLER_CONFIG["jin10_flash"]["url"]
    try:
        headers = {
            "x-app-id": "bVBF4FyRTn5NJF5n",
            "x-version": "1.0.0",
        }
        resp = _session().get(
            url, params={"channel": "-8200", "vip": "1"}, headers=headers, timeout=15
        )
        items = resp.json().get("data") or []
        events = []
        for item in items:
            inner = item.get("data") or {}
            star = item.get("important")  # 1-5 星重要性（VIP 接口；免费接口为 0）
            ev = _normalize(
                (inner.get("title") or "").strip() or (inner.get("content") or "")[:50],
                inner.get("content") or "",
                f"https://www.jin10.com/flash/{item.get('id', '')}",
                item.get("time") or inner.get("time"),
                importance_hint=int(star) if star else None,
                source="金十数据",
            )
            if ev and ev["title"]:
                events.append(ev)
        return events
    except Exception as e:
        logger.warning(f"金十数据快讯抓取失败: {e}")
        return []


def _fetch_sina_724():
    """新浪财经 7x24（备源）。"""
    url = CRAWLER_CONFIG["sina_724"]["url"]
    try:
        resp = _session().get(
            url,
            params={"page": 1, "page_size": 30, "zhibo_id": 152,
                    "tag_id": 0, "dire": "f", "dpc": 1},
            timeout=15,
        )
        feed = (((resp.json().get("result") or {}).get("data") or {}).get("feed") or {})
        events = []
        for item in feed.get("list") or []:
            rich = item.get("rich_text") or item.get("title") or ""
            plain = re.sub(r"<[^>]+>", "", rich)
            ev = _normalize(
                plain[:50],
                plain,
                item.get("docurl") or "",
                item.get("create_time"),
                source="新浪7x24",
            )
            if ev and ev["title"]:
                events.append(ev)
        return events
    except Exception as e:
        logger.warning(f"新浪 7x24 抓取失败: {e}")
        return []


def _fetch_eastmoney_flash():
    """东方财富快讯（备源）。"""
    url = CRAWLER_CONFIG["eastmoney_flash"]["url"]
    try:
        resp = _session().get(
            url,
            params={"client": "web", "biz": "web_724", "fastColumn": "102",
                    "sortEnd": "", "pageSize": 30, "req_trace": ""},
            timeout=15,
        )
        items = ((resp.json().get("data") or {}).get("fastNewsList") or [])
        events = []
        for item in items:
            ev = _normalize(
                item.get("title") or item.get("summary", "")[:50],
                item.get("summary") or "",
                f"https://finance.eastmoney.com/a/{item.get('code', '')}.html",
                item.get("showTime"),
                source="东财快讯",
            )
            if ev and ev["title"]:
                events.append(ev)
        return events
    except Exception as e:
        logger.warning(f"东方财富快讯抓取失败: {e}")
        return []


_SOURCE_FETCHERS = {
    "cls_telegraph": _fetch_cls_telegraph,
    "jin10_flash": _fetch_jin10_flash,
    "sina_724": _fetch_sina_724,
    "eastmoney_flash": _fetch_eastmoney_flash,
}


# ==================== 对外接口 ====================

def fetch_events_from_crawler() -> list[dict]:
    """从全部启用的快讯源抓取事件（3.1.1 接口）。

    返回标准化事件列表（含 title/content/source_url/announced_at/
    importance_hint/source），按时间倒序。
    """
    events = []
    for name, cfg in CRAWLER_CONFIG.items():
        if not cfg.get("enabled", False):
            continue
        fetched = _SOURCE_FETCHERS[name]()
        logger.info(f"[事件采集] {name}: {len(fetched)} 条")
        events.extend(fetched)
    # 按标题去重（同源/跨源转载）
    seen = set()
    deduped = []
    for ev in events:
        key = ev["title"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)
    deduped.sort(key=lambda e: e["announced_at"], reverse=True)
    return deduped


def _title_hash(title: str) -> str:
    return hashlib.md5(title.strip().encode("utf-8")).hexdigest()[:16]


def _already_handled(conn, titles: list[str]) -> set[str]:
    """查询 PG 中已处理（approved/ignored）的同标题事件，返回标题集合。"""
    if not titles:
        return set()
    try:
        rows = conn.execute(
            "SELECT DISTINCT title FROM events WHERE title = ANY(%s)",
            (titles,),
        ).fetchall()
        return {r[0] for r in rows}
    except Exception as e:
        logger.warning(f"事件去重查询失败: {e}")
        return set()


def save_pending_events(events: list[dict], conn=None) -> list[int]:
    """将爬虫事件写入 Redis 待审核草稿（events:pending:{draft_id}）。

    - 对 PG 已处理事件（approved/ignored）与 Redis 已有草稿做标题去重
    - draft_id 由 Redis 计数器 events:draft_seq 分配
    - 返回新写入的 draft_id 列表
    """
    if not events:
        return []
    if not is_redis_available():
        logger.warning("Redis 不可用，事件草稿无法保存")
        return []

    # PG 去重（conn 为 None 时跳过，仅按 Redis 去重）
    titles = [e["title"] for e in events]
    handled = set()
    if conn is not None:
        handled = _already_handled(conn, titles)

    r = get_redis_client()
    new_ids = []
    for ev in events:
        if ev["title"] in handled:
            continue
        # Redis 草稿去重：遍历现有 pending 草稿检查标题
        dup = False
        for key in r.scan_iter(f"{KEY_PENDING_EVENT.format(draft_id='*')}", count=100):
            try:
                draft = json.loads(r.get(key) or "{}")
                if draft.get("title") == ev["title"]:
                    dup = True
                    break
            except (json.JSONDecodeError, TypeError):
                continue
        if dup:
            continue
        draft_id = int(r.incr(KEY_DRAFT_SEQ))
        ev["draft_id"] = draft_id
        r.set(KEY_PENDING_EVENT.format(draft_id=draft_id),
              json.dumps(ev, ensure_ascii=False), ex=PENDING_DRAFT_TTL)
        new_ids.append(draft_id)
    logger.info(f"[事件采集] 新写入待审草稿 {len(new_ids)} 条（抓取 {len(events)} 条）")
    return new_ids
