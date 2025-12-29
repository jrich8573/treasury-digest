import os
import pytest

from treasury_digest import fetch_treasury_news


@pytest.mark.skipif(
    not os.environ.get("NEWSAPI_AI_KEY") and not os.environ.get("NEWS_API_KEY"),
    reason="NEWSAPI_AI_KEY/NEWS_API_KEY not set",
)
def test_fetch_treasury_news_runs_and_returns_list(monkeypatch):
    # Configure env for broad coverage and stability
    monkeypatch.setenv(
        "QUERY",
        '"United States Treasury" OR "Treasury Department" OR "Federal Reserve" OR "IRS"',
    )
    monkeypatch.setenv("ALLOW_DOMAINS", "")  # no domain filter
    monkeypatch.setenv("NEWS_LOOKBACK_DAYS", "7")
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("DEBUG", "1")
    monkeypatch.setenv("VERIFY_EMPTY_RESULTS", "1")

    articles = fetch_treasury_news()
    assert isinstance(articles, list)
    if articles:
        a = articles[0]
        for key in ("title", "description", "source", "url", "published_at"):
            assert key in a
