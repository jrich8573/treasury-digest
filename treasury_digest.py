import os
import smtplib
import ssl
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import requests
from eventregistry import EventRegistry, QueryArticlesIter, QueryItems, ReturnInfo, ArticleInfoFlags

# ------------- CONFIG ------------- #

def _env(name, default=None):
    value = os.environ.get(name)
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value.strip()

def _require_any_env(names: list[str]) -> str:
    for n in names:
        v = os.environ.get(n)
        if v is not None and str(v).strip():
            return str(v).strip()
    raise RuntimeError(f"Missing required environment variable (any of): {', '.join(names)}")


def _parse_email_list(raw: str) -> list[str]:
    # Accept comma/semicolon/newline separated addresses.
    parts = []
    for token in raw.replace(";", ",").replace("\n", ",").split(","):
        t = token.strip()
        if t:
            parts.append(t)
    # De-dupe while preserving order
    seen = set()
    out = []
    for p in parts:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def _is_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


NEWSAPI_AI_KEY = _require_any_env(["NEWSAPI_AI_KEY", "NEWS_API_KEY"])  # prefer NEWSAPI_AI_KEY

# SMTP / email settings
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_SECURITY = _env("SMTP_SECURITY", "starttls").strip().lower()  # starttls | ssl | none
_smtp_port_raw = os.environ.get("SMTP_PORT")
if _smtp_port_raw is None or not str(_smtp_port_raw).strip():
    SMTP_PORT = 465 if SMTP_SECURITY == "ssl" else 587
else:
    SMTP_PORT = int(str(_smtp_port_raw).strip())
SMTP_USER = _require_env("SMTP_USER")                    # your email / SMTP username
SMTP_PASS = _require_env("SMTP_PASS")                    # app password / SMTP credential
FROM_EMAIL = _env("FROM_EMAIL", SMTP_USER).strip()
TO_EMAILS = _parse_email_list(_require_env("TO_EMAILS"))  # comma-separated list

# Optional runtime toggles
DRY_RUN = _is_truthy(_env("DRY_RUN"))
DEBUG = _is_truthy(_env("DEBUG"))
VERIFY_EMPTY_RESULTS = _is_truthy(_env("VERIFY_EMPTY_RESULTS", "1"))

# Search parameters
QUERY = _env(
    "QUERY",
    '"United States Treasury" OR "U.S. Treasury" OR "Treasury Department" OR "IRS" OR "Internal Revenue Service" OR "FRB" OR "Federal Reserve Board" OR "Federal Reserve" OR "Fiscal Policy" OR "Monetary Policy" OR "Economic Policy" OR "Economic Outlook" OR "Economic Data" OR "Stock Market" OR "United States Stock Market" OR "NYSE" OR "NASDAQ"',
)
# Domain allowlist (applied locally after fetching). Prefer ALLOW_DOMAINS, fallback to legacy SOURCES.
_default_domains = ",".join(
    [
        "reuters.com",
        # Premium/paywalled domains below may yield few/no matches from EventRegistry
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        # More broadly available outlets
        "cnbc.com",
        "marketwatch.com",
        "barrons.com",
        "finance.yahoo.com",
        "investing.com",
        "seekingalpha.com",
        # Useful official sources
        "treasury.gov",
        "federalreserve.gov",
    ]
)
SOURCES = _env("ALLOW_DOMAINS", _env("SOURCES", _default_domains))  # override with env var if desired
MAX_ARTICLES = int(_env("MAX_ARTICLES", "50"))
NEWS_LOOKBACK_DAYS = int(_env("NEWS_LOOKBACK_DAYS", "1"))
NEWSAPI_KEYWORD_LIMIT = int(_env("NEWSAPI_KEYWORD_LIMIT", "15"))

# LLM parameters (free/local via Ollama by default)
LLM_PROVIDER = _env("LLM_PROVIDER", "ollama").strip().lower()  # "ollama"
LLM_MAX_TOKENS = int(_env("LLM_MAX_TOKENS", "1800"))
LLM_TEMPERATURE = float(_env("LLM_TEMPERATURE", "0.4"))

# Ollama settings
OLLAMA_BASE_URL = _env("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_TIMEOUT_SECONDS = int(_env("OLLAMA_TIMEOUT_SECONDS", "120"))


# ------------- NEWS FETCHER ------------- #

def fetch_treasury_news():
    now = datetime.now(timezone.utc)
    date_end = now.date().isoformat()
    date_start = (now - timedelta(days=NEWS_LOOKBACK_DAYS)).date().isoformat()

    def _normalize_query(q: str) -> str:
        # Normalize boolean operators; also trim whitespace/newlines.
        q2 = q.replace(" or ", " OR ").replace(" and ", " AND ").replace(" not ", " NOT ")
        return " ".join(q2.split())

    def _split_or_terms(q: str) -> list[str]:
        # Convert OR-style query strings into a keyword list for newsapi.ai.
        # Supports separators: OR, comma, or pipe.
        q_norm = _normalize_query(q)
        for sep in ["|", ","]:
            q_norm = q_norm.replace(sep, " OR ")
        parts = [p.strip() for p in q_norm.split(" OR ") if p.strip()]
        cleaned = []
        for p in parts:
            # drop surrounding quotes if present
            if (p.startswith('"') and p.endswith('"')) or (p.startswith("'") and p.endswith("'")):
                p = p[1:-1]
            p = p.strip()
            if p:
                cleaned.append(p)
        # De-dupe while preserving order
        seen = set()
        out = []
        for c in cleaned:
            if c not in seen:
                out.append(c)
                seen.add(c)
        return out

    def _parse_domains(raw: str | None) -> list[str]:
        if not raw:
            return []
        return [d.strip().lower() for d in raw.split(",") if d.strip()]

    def _domain_allowed(url: str, allowlist: list[str]) -> bool:
        if not allowlist:
            return True
        try:
            host = urlparse(url).netloc.lower()
        except Exception:
            return False
        if host.startswith("www."):
            host = host[4:]
        return any(host == d or host.endswith("." + d) for d in allowlist)

    keywords = _split_or_terms(QUERY)
    if not keywords:
        keywords = ["United States Treasury"]

    # Respect provider subscription limits on max keywords.
    if len(keywords) > NEWSAPI_KEYWORD_LIMIT:
        # Prefer high-signal Treasury/Fed terms first, then fill remaining order-preserving.
        priority = [
            "United States Treasury",
            "Treasury Department",
            "U.S. Treasury",
            "Internal Revenue Service",
            "IRS",
            "Federal Reserve",
            "Federal Reserve Board",
            "FRB",
            "Fiscal Policy",
            "Monetary Policy",
            "Economic Policy",
        ]
        selected = []
        seen = set()
        # Add priority terms that are present
        for p in priority:
            if p in keywords and p not in seen:
                selected.append(p)
                seen.add(p)
                if len(selected) >= NEWSAPI_KEYWORD_LIMIT:
                    break
        # Fill with remaining keywords in original order
        if len(selected) < NEWSAPI_KEYWORD_LIMIT:
            for k in keywords:
                if k not in seen:
                    selected.append(k)
                    seen.add(k)
                    if len(selected) >= NEWSAPI_KEYWORD_LIMIT:
                        break
        keywords = selected

    # Pull more than MAX_ARTICLES since we may filter by SOURCES domains afterwards
    fetch_max = min(200, max(MAX_ARTICLES, 1) * 3)
    allow_domains = _parse_domains(SOURCES)

    er = EventRegistry(apiKey=NEWSAPI_AI_KEY)
    q = QueryArticlesIter(
        keywords=QueryItems.OR(keywords),
        lang="eng",
        dateStart=date_start,
        dateEnd=date_end,
    )

    def _collect_articles(allow_domains_list: list[str]) -> tuple[list[dict], int, int]:
        articles_local: list[dict] = []
        seen_urls_local: set[str] = set()
        fetched_local = 0
        kept_local = 0
        for art in q.execQuery(
            er,
            sortBy="date",
            maxItems=fetch_max,
            returnInfo=ReturnInfo(articleInfo=ArticleInfoFlags(basicInfo=True, body=True, sourceInfo=True)),
        ):
            fetched_local += 1
            url_a = art.get("url")
            if not url_a or url_a in seen_urls_local:
                continue
            if not _domain_allowed(url_a, allow_domains_list):
                continue
            seen_urls_local.add(url_a)
            kept_local += 1
            articles_local.append(
                {
                    "title": art.get("title"),
                    "description": art.get("body") or art.get("summary") or art.get("title"),
                    "source": (art.get("source") or {}).get("title") or (art.get("source") or {}).get("uri"),
                    "url": url_a,
                    "published_at": art.get("dateTime") or art.get("date"),
                }
            )
            if len(articles_local) >= MAX_ARTICLES:
                break
        return articles_local, fetched_local, kept_local

    # First pass: with allowlist
    articles, fetched, kept = _collect_articles(allow_domains)

    # Fallback: if nothing kept due to strict domains, try without domain filtering
    fallback_used = False
    if not articles and allow_domains:
        fallback_used = True
        articles, fetched, kept = _collect_articles([])

    # Keep deterministic ordering (newest first)
    articles.sort(key=lambda x: x.get("published_at") or "", reverse=True)

    if DEBUG:
        print(
            "newsapi.ai debug: "
            f"lookback_days={NEWS_LOOKBACK_DAYS}, dateStart={date_start}, dateEnd={date_end}, "
            f"keywords={len(keywords)} (limit={NEWSAPI_KEYWORD_LIMIT}), allow_domains={len(allow_domains)}, "
            f"fetched={fetched}, kept={kept}, returned={len(articles)}, "
            f"fallback_no_domains_used={fallback_used}"
        )

    # Verification: if empty, do a cheap sanity check to confirm this is accurate.
    if VERIFY_EMPTY_RESULTS and not articles:
        try:
            sanity_keywords = ["Treasury", "Federal Reserve", "IRS"]
            sanity_1d = QueryArticlesIter(
                keywords=QueryItems.OR(sanity_keywords),
                lang="eng",
                dateStart=(now - timedelta(days=1)).date().isoformat(),
                dateEnd=date_end,
            )
            sanity_7d = QueryArticlesIter(
                keywords=QueryItems.OR(sanity_keywords),
                lang="eng",
                dateStart=(now - timedelta(days=7)).date().isoformat(),
                dateEnd=date_end,
            )
            sanity_1d_count = 0
            for _ in sanity_1d.execQuery(er, sortBy="date", maxItems=1):
                sanity_1d_count += 1
            sanity_7d_count = 0
            for _ in sanity_7d.execQuery(er, sortBy="date", maxItems=1):
                sanity_7d_count += 1
            print(
                "newsapi.ai empty-results check: "
                f"your_keywords={len(keywords)} allow_domains={len(allow_domains)}; "
                f"sanity_has_results_1d={sanity_1d_count > 0}; "
                f"sanity_has_results_7d={sanity_7d_count > 0}; "
                "If sanity is true but your results are empty, try: "
                "(1) clear ALLOW_DOMAINS/SOURCES, (2) simplify QUERY to fewer phrases, "
                "(3) increase NEWS_LOOKBACK_DAYS."
            )
        except Exception as e:
            print(f"newsapi.ai empty-results check failed: {e}")

    return articles


# ------------- GPT CURATOR ------------- #

def _ollama_chat(system_prompt: str, user_prompt: str) -> str:
    """
    Call a local Ollama server (free) using the chat API.
    Docs: https://github.com/ollama/ollama/blob/main/docs/api.md
    """
    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": LLM_TEMPERATURE,
            # Ollama uses num_predict (approx) instead of max_tokens
            "num_predict": LLM_MAX_TOKENS,
        },
    }
    resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT_SECONDS)
    resp.raise_for_status()
    data = resp.json()
    msg = (data.get("message") or {}).get("content")
    if not msg:
        raise RuntimeError("Ollama response missing message.content")
    return msg


def _curate_with_llm(system_prompt: str, user_prompt: str) -> str:
    if LLM_PROVIDER == "ollama":
        return _ollama_chat(system_prompt=system_prompt, user_prompt=user_prompt)
    raise RuntimeError(f"Unsupported LLM_PROVIDER: {LLM_PROVIDER}. Supported: ollama")


def curate_with_gpt(articles):
    """Use an LLM to curate and summarize Treasury news."""
    if not articles:
        return "No significant U.S. Treasury news found in the last 24 hours."

    # Create a compact plain-text representation of the articles
    article_block_lines = []
    for i, a in enumerate(articles, start=1):
        line = textwrap.dedent(f"""
        [{i}] {a['title']}
        Source: {a['source']} | Published: {a['published_at']}
        Summary: {a['description']}
        URL: {a['url']}
        """).strip()
        article_block_lines.append(line)

    article_block = "\n\n".join(article_block_lines)

    system_prompt = (
        "You are a professional financial journalist and policy analyst who "
        "curates news about the U.S. Treasury for senior decision-makers. "
        "Your tone is concise, neutral, and insight-driven."
    )

    user_prompt = f"""
I will give you a list of recent news articles related to the United States Treasury.

Articles:
{article_block}

Tasks:
1. Identify the 3–7 most important themes or stories.
2. For each, provide:
   - A short headline in plain English.
   - 2–4 sentence summary in business / policy terms.
   - Mention specific Treasury actions, policy changes, or market impacts if applicable.
3. Add a brief 'Market & Policy Takeaways' section (3–5 bullet points).
4. Group related articles when they cover the same story; reference their article indices in brackets (e.g., [1, 3, 5]).

Output in **well-structured Markdown** suitable for an email body, with clear headings, bullet points, and embedded URLs where useful.
"""
    return _curate_with_llm(system_prompt=system_prompt, user_prompt=user_prompt)


# ------------- EMAIL BUILDER ------------- #

def markdown_to_basic_html(md_text):
    """
    Very naive Markdown -> HTML converter for emails.
    For production use, consider a proper library like markdown2 or mistune.
    """
    # Replace simple patterns: headings and bullets (minimal)
    html = md_text

    # Headings: lines starting with "#"
    lines = html.splitlines()
    converted = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("### "):
            converted.append(f"<h3>{stripped[4:].strip()}</h3>")
        elif stripped.startswith("## "):
            converted.append(f"<h2>{stripped[3:].strip()}</h2>")
        elif stripped.startswith("# "):
            converted.append(f"<h1>{stripped[2:].strip()}</h1>")
        elif stripped.startswith("- "):
            converted.append(f"<li>{stripped[2:].strip()}</li>")
        else:
            converted.append(f"<p>{line}</p>")

    html = "\n".join(converted)

    # Wrap <li> items into <ul> blocks (very naive)
    if "<li>" in html:
        html = html.replace("<li>", "<ul><li>", 1)
        html = html[::-1].replace(">il/<", ">lu/<", 1)[::-1]  # close last </ul>

    return f"<!DOCTYPE html><html><body>{html}</body></html>"


def build_email(curated_markdown):
    subject = f"U.S. Treasury News Brief – {datetime.now().strftime('%Y-%m-%d')}"
    html_body = markdown_to_basic_html(curated_markdown)
    text_body = curated_markdown  # okay as a plain-text fallback

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = FROM_EMAIL
    msg["To"] = ", ".join(TO_EMAILS)

    part1 = MIMEText(text_body, "plain")
    part2 = MIMEText(html_body, "html")

    msg.attach(part1)
    msg.attach(part2)

    return msg


# ------------- EMAIL SENDER ------------- #

def send_email(msg):
    try:
        if SMTP_SECURITY == "ssl":
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                server.login(SMTP_USER, SMTP_PASS)
                server.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())
            return

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.ehlo()
            if SMTP_SECURITY == "starttls":
                context = ssl.create_default_context()
                server.starttls(context=context)
                server.ehlo()
            elif SMTP_SECURITY == "none":
                pass
            else:
                raise RuntimeError(
                    f"Unsupported SMTP_SECURITY: {SMTP_SECURITY}. Supported: starttls, ssl, none"
                )
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise RuntimeError(
            "SMTP authentication failed (535). If using Gmail, you typically must use an App Password "
            "(requires 2-Step Verification) instead of your normal password. "
            "Also ensure SMTP_SECURITY/SMTP_PORT are correct for your provider."
        ) from e


# ------------- MAIN RUNNER ------------- #

def run_treasury_news_digest():
    articles = fetch_treasury_news()
    curated_md = curate_with_gpt(articles)
    if DRY_RUN:
        print(curated_md)
        return

    msg = build_email(curated_md)
    send_email(msg)


if __name__ == "__main__":
    run_treasury_news_digest()
