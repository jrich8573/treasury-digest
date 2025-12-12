import os
import smtplib
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import requests
from openai import OpenAI

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


NEWS_API_KEY = _require_env("NEWS_API_KEY")              # NewsAPI.org key
OPENAI_API_KEY = _require_env("OPENAI_API_KEY")          # OpenAI key

# SMTP / email settings
SMTP_HOST = _env("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(_env("SMTP_PORT", "587"))
SMTP_USER = _require_env("SMTP_USER")                    # your email / SMTP username
SMTP_PASS = _require_env("SMTP_PASS")                    # app password / SMTP credential
FROM_EMAIL = _env("FROM_EMAIL", SMTP_USER).strip()
TO_EMAILS = _parse_email_list(_require_env("TO_EMAILS"))  # comma-separated list

# Optional runtime toggles
DRY_RUN = _is_truthy(_env("DRY_RUN"))

# Search parameters
QUERY = _env("QUERY", '"United States Treasury" OR "U.S. Treasury" OR "Treasury Department"')
SOURCES = _env("SOURCES")  # e.g. "bloomberg.com,wsj.com,nytimes.com"
MAX_ARTICLES = int(_env("MAX_ARTICLES", "25"))

# OpenAI parameters
OPENAI_MODEL = _env("OPENAI_MODEL", "gpt-4.1-mini")
OPENAI_MAX_TOKENS = int(_env("OPENAI_MAX_TOKENS", "1800"))
OPENAI_TEMPERATURE = float(_env("OPENAI_TEMPERATURE", "0.4"))


# ------------- NEWS FETCHER ------------- #

def fetch_treasury_news():
    """Fetch recent U.S. Treasury-related news from NewsAPI."""
    url = "https://newsapi.org/v2/everything"
    now = datetime.now(timezone.utc)
    from_date = (now - timedelta(days=1)).isoformat()

    params = {
        "q": QUERY,
        "from": from_date,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": MAX_ARTICLES,
        "apiKey": NEWS_API_KEY,
    }
    if SOURCES:
        params["domains"] = SOURCES

    resp = requests.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()

    articles_raw = data.get("articles", [])
    articles = []
    seen_urls = set()

    for a in articles_raw:
        url_a = a.get("url")
        if not url_a or url_a in seen_urls:
            continue
        seen_urls.add(url_a)

        articles.append({
            "title": a.get("title"),
            "description": a.get("description"),
            "source": a.get("source", {}).get("name"),
            "url": url_a,
            "published_at": a.get("publishedAt"),
        })

    return articles


# ------------- GPT CURATOR ------------- #

def curate_with_gpt(articles):
    """Use GPT to curate and summarize Treasury news."""
    if not articles:
        return "No significant U.S. Treasury news found in the last 24 hours."

    client = OpenAI(api_key=OPENAI_API_KEY)

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

    response = client.chat.completions.create(
        model=OPENAI_MODEL,  # e.g. gpt-4.1-mini / gpt-4.1 depending on your access
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=OPENAI_MAX_TOKENS,
        temperature=OPENAI_TEMPERATURE,
    )

    curated_markdown = response.choices[0].message.content
    return curated_markdown


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
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(FROM_EMAIL, TO_EMAILS, msg.as_string())


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
