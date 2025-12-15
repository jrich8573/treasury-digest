## Treasury News Digest

This repo contains a Python script (`treasury_digest.py`) that:

- **Fetches** the last ~24 hours of U.S. Treasury-related news from **NewsAPI**
- **Curates + summarizes** the most important themes using a **free local LLM (Ollama)**
- **Emails** the resulting brief via **SMTP** (HTML + plain text)

## Automation (GitHub Actions)

The workflow `.github/workflows/treasury_digest.yml` runs the script:

- **On a daily schedule** (cron) and
- **Manually** via the “Run workflow” button (`workflow_dispatch`)

All sensitive values (API keys, SMTP password, recipient emails) are read from **GitHub Secrets / Variables**—nothing is hardcoded.

## Configuration (required)

### Step 1: Get your external credentials

- **NewsAPI key**: create an API key on NewsAPI.org
- **SMTP credentials**:
  - If using Gmail:
    - Turn on **2‑Step Verification**
    - Create an **App Password** and use that as `SMTP_PASS` (a normal Gmail password will fail in Actions with `535 5.7.8 BadCredentials`)
    - Use `SMTP_HOST=smtp.gmail.com`, `SMTP_SECURITY=starttls`, `SMTP_PORT=587` (default)
    - Alternatively: `SMTP_SECURITY=ssl`, `SMTP_PORT=465`
  - Or use another SMTP provider (SendGrid, Mailgun SMTP, etc.)

### Step 2: Add GitHub Secrets

In your GitHub repo, go to **Settings → Secrets and variables → Actions → Secrets** and add:

- **`NEWS_API_KEY`**: NewsAPI key
- **`SMTP_USER`**: SMTP username (often your email address)
- **`SMTP_PASS`**: SMTP password / app password
- **`TO_EMAILS`**: comma-separated recipient list (example: `person1@acme.com,person2@acme.com`)

Optional (Secrets):

- **`FROM_EMAIL`**: if you want “From” to differ from `SMTP_USER`

### Step 3: Add GitHub Variables (optional)

Go to **Settings → Secrets and variables → Actions → Variables** and add any of these if you want to override defaults:

- **`SMTP_HOST`**: default `smtp.gmail.com`
- **`SMTP_PORT`**: default `587`
- **`SMTP_SECURITY`**: default `starttls` (supported: `starttls`, `ssl`, `none`)
- **`QUERY`**: the NewsAPI query (default is a Treasury-focused query)
- **`SOURCES`**: optional domain list (example: `wsj.com,nytimes.com`)
- **`MAX_ARTICLES`**: default `25`
- **`LLM_PROVIDER`**: default `ollama`
- **`OLLAMA_BASE_URL`**: default `http://localhost:11434`
- **`OLLAMA_MODEL`**: default `llama3.2:3b`
- **`OLLAMA_TIMEOUT_SECONDS`**: default `120`
- **`LLM_MAX_TOKENS`**: default `1800`
- **`LLM_TEMPERATURE`**: default `0.4`

## Running locally

### Prerequisites

- Python **3.10+** (recommended: 3.11)

### Step 1: Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Step 2: Set environment variables

You can export env vars in your shell (or use a `.env` loader of your choice).

Required:

- `NEWS_API_KEY`
- `SMTP_USER`
- `SMTP_PASS`
- `TO_EMAILS`

Optional:

- `FROM_EMAIL`
- `SMTP_HOST` (default `smtp.gmail.com`)
- `SMTP_PORT` (default `587`)
- `SMTP_SECURITY` (default `starttls`)
- `QUERY`, `SOURCES`, `MAX_ARTICLES`
- `LLM_PROVIDER` (default `ollama`)
- `OLLAMA_BASE_URL` (default `http://localhost:11434`)
- `OLLAMA_MODEL` (default `llama3.2:3b`)
- `OLLAMA_TIMEOUT_SECONDS` (default `120`)
- `LLM_MAX_TOKENS`, `LLM_TEMPERATURE`

### Step 3: Run

```bash
python treasury_digest.py
```

### Dry run (no email sent)

```bash
DRY_RUN=1 python treasury_digest.py
```

## Notes / troubleshooting

- **Missing env vars**: the script fails fast with a clear error if required values are not set.
- **SMTP**: some providers require TLS/STARTTLS and/or “less secure app” settings. Prefer app passwords where supported.

