from AI.eventStudy.collectors import event_crawler


def test_saturated_news_first_page_is_reported_as_partial(monkeypatch):
    rows = [
        {"title": f"新闻 {index}", "content": "正文", "announced_at": "2026-09-23T09:00:00+08:00"}
        for index in range(event_crawler._SOURCE_PAGE_LIMITS["cls_telegraph"])
    ]
    monkeypatch.setattr(event_crawler, "CRAWLER_CONFIG", {"cls_telegraph": {"enabled": True}})
    monkeypatch.setitem(event_crawler._SOURCE_FETCHERS, "cls_telegraph", lambda: rows)

    batch = event_crawler.fetch_source_batch()

    assert batch["status"] == "partial"
    assert batch["coverage"]["incomplete"] == 1
    assert batch["coverage"]["succeeded"] == 1
    assert batch["sources"][0]["coverage_status"] == "page_full"
    assert batch["sources"][0]["has_more"] is True


def test_unsaturated_news_page_is_reported_complete(monkeypatch):
    monkeypatch.setattr(event_crawler, "CRAWLER_CONFIG", {"cls_telegraph": {"enabled": True}})
    monkeypatch.setitem(
        event_crawler._SOURCE_FETCHERS,
        "cls_telegraph",
        lambda: [{"title": "一条新闻", "content": "正文", "announced_at": "2026-09-23T09:00:00+08:00"}],
    )

    batch = event_crawler.fetch_source_batch()

    assert batch["status"] == "ok"
    assert batch["coverage"]["incomplete"] == 0
    assert batch["sources"][0]["coverage_complete"] is True
