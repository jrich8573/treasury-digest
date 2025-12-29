import os
import pytest

import treasury_digest as td


@pytest.mark.skipif(
    not os.environ.get("NEWSAPI_AI_KEY") and not os.environ.get("NEWS_API_KEY"),
    reason="NEWSAPI_AI_KEY/NEWS_API_KEY not set",
)
def test_fetch_treasury_news_runs_and_returns_list(monkeypatch):
    # Configure module-level overrides for stability and to avoid import-time env binding issues.
    monkeypatch.setattr(td, "QUERY", '"United States Treasury" OR "Treasury Department" OR "Federal Reserve" OR "IRS"', False)
    monkeypatch.setattr(td, "SOURCES", "", False)  # no domain filter
    monkeypatch.setattr(td, "NEWS_LOOKBACK_DAYS", 7, False)
    monkeypatch.setattr(td, "NEWSAPI_KEYWORD_LIMIT", 12, False)

    # Optional runtime toggles via env
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("VERIFY_EMPTY_RESULTS", "1")

    articles = td.fetch_treasury_news()
    assert isinstance(articles, list)
    if articles:
        a = articles[0]
        for key in ("title", "description", "source", "url", "published_at"):
            assert key in a
