import os
import smtplib
import ssl
import textwrap
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

import requests

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

# Search parameters
QUERY = _env("QUERY", '"United States Treasury" OR "U.S. Treasury" OR "Treasury Department"')
SOURCES = _env("SOURCES")  # e.g. "bloomberg.com,wsj.com,nytimes.com"
MAX_ARTICLES = int(_env("MAX_ARTICLES", "25"))

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
