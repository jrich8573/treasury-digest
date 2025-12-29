import os
from datetime import datetime, timedelta

import pytest
from eventregistry import EventRegistry, QueryArticlesIter, QueryItems


@pytest.mark.skipif(
    not os.environ.get("NEWSAPI_AI_KEY") and not os.environ.get("NEWS_API_KEY"),
    reason="NEWSAPI_AI_KEY/NEWS_API_KEY not set",
)
def test_eventregistry_api_returns_results_broad_query():
    er = EventRegistry(apiKey=os.environ.get("NEWSAPI_AI_KEY") or os.environ.get("NEWS_API_KEY"))
    now = datetime.utcnow()
    q = QueryArticlesIter(
        keywords=QueryItems.OR(["Apple", "Tesla", "Microsoft"]),
        lang="eng",
        dateStart=(now - timedelta(days=7)).date().isoformat(),
        dateEnd=now.date().isoformat(),
    )
    count = 0
    for _ in q.execQuery(er, sortBy="date", maxItems=3):
        count += 1
        if count >= 1:
            break
    assert count >= 1, "EventRegistry should return at least one article for broad query in 7 days"
