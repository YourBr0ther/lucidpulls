<p align="center">
  <img src="assets/logo.svg" alt="LucidPulls" width="140" height="140">
</p>

<h1 align="center">LucidPulls</h1>

<p align="center">
  <strong>Wake up to bug fixes, not bug reports.</strong><br>
  <sub>An automated nightly code review agent that finds bugs, creates PRs, and delivers a morning summary — so your codebase improves while you sleep.</sub>
</p>

<br>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-3776ab?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.11+"></a>&nbsp;
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green?style=for-the-badge" alt="MIT License"></a>&nbsp;
  <img src="https://img.shields.io/badge/docker-ready-2496ed?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Ready">
</p>

<p align="center">
  <a href="#-features">Features</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#-quick-start">Quick Start</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#-how-it-works">How It Works</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#%EF%B8%8F-configuration">Configuration</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#-llm-providers">LLM Providers</a>
</p>

<br>

---

<br>

## Why LucidPulls?

Most bugs aren't hard to fix — they're hard to *find*. Null checks, unclosed resources, off-by-one errors, inverted conditions. They hide in plain sight and surface at the worst possible time.

LucidPulls runs overnight, reviews your repos with an LLM, and opens pull requests for conservative, high-confidence fixes. You review the PRs over coffee. That's it.

<br>

## Features

| | Feature | Description |
|:---:|:---|:---|
| **Scheduled** | **Nightly Review** | Runs on a configurable schedule — start time, deadline, and report delivery |
| **Analysis** | **Smart File Selection** | Priority-scored file selection (entry points first, tests last) with LLM analysis |
| **Fixes** | **Automated PRs** | Creates branches, commits, and pull requests for high-confidence bug fixes |
| **Flexible** | **Multiple LLM Backends** | Azure AI Studios, NanoGPT, or Ollama for fully local, private analysis |
| **Reports** | **Morning Notifications** | Summary of all PRs, LLM token usage, and findings via Discord or Teams |
| **Resilient** | **Failure Alerting** | Sends a notification when a run completes with zero PRs across all repos |
| **Safe** | **Dry-Run Mode** | Full pipeline minus push/PR creation for safe testing and validation |

<br>

## Quick Start

### Prerequisites

- Python 3.11+
- GitHub Personal Access Token
- SSH key configured for GitHub
- Docker *(optional, for containerized deployment)*

### Install

```bash
git clone git@github.com:YourBr0ther/lucidpulls.git
cd lucidpulls
cp .env.example .env       # configure your settings
pip install -r requirements.txt
```

### Run

```bash
python -m src.main                      # start the scheduled service
python -m src.main --run-now            # run a review immediately
python -m src.main --run-now --dry-run  # analyze without pushing (best for testing)
python -m src.main --send-report        # send today's report now
python -m src.main --test-notifications # verify webhook delivery
python -m src.main --health-check       # Docker health check (exit 0/1)
python -m src.main --debug              # enable debug logging
```

### Docker

```bash
docker-compose up -d       # build and start
docker-compose logs -f     # follow logs
docker-compose down        # stop
```

<br>

## How It Works

```
  02:00 AM                                                     07:00 AM
  ─────────────────────────────────────────────────────────────────────
     │                                                            │
     ▼                                                            ▼
  ┌──────────────────────────────────────┐    ┌──────────────────────┐
  │         FOR EACH REPOSITORY          │    │    MORNING REPORT    │
  │                                      │    │                      │
  │  1. Clone or pull latest code        │    │  Summary of all PRs  │
  │  2. Fetch open issues (bug/enhance)  │───▶│  sent to Discord or  │
  │  3. Analyze code with LLM            │    │  Microsoft Teams     │
  │  4. Validate high-confidence fixes   │    │                      │
  │  5. Create branch, commit, push      │    └──────────────────────┘
  │  6. Open pull request                │
  │                                      │
  └──────────────────────────────────────┘
```

<br>

## What Gets Fixed

LucidPulls targets conservative, high-confidence fixes only. No style changes, no refactoring, no new features.

| Fix Type | Examples |
|:---|:---|
| **Null checks** | Missing `None` / `null` guards before access |
| **Error handling** | Uncaught exceptions, bare `except` blocks |
| **Off-by-one errors** | Array bounds, loop exit conditions |
| **Logic typos** | Wrong operators (`&&` vs `\|\|`), inverted conditions |
| **Resource leaks** | Unclosed files, database connections, sockets |
| **Security issues** | SQL injection, path traversal, obvious vulnerabilities |

<br>

## Configuration

Create a `.env` file (or copy `.env.example`) with the following:

```bash
# --- Repositories ---
REPOS=owner/repo1,owner/repo2

# --- GitHub ---
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
GITHUB_USERNAME=YourUsername
GITHUB_EMAIL=your@email.com
SSH_KEY_PATH=~/.ssh/id_rsa          # optional, defaults to ~/.ssh/id_rsa

# --- LLM Provider (azure | nanogpt | ollama) ---
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama

# --- Notifications (discord | teams) ---
NOTIFICATION_CHANNEL=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# --- Schedule (24-hour format) ---
SCHEDULE_START=02:00
SCHEDULE_DEADLINE=06:00
REPORT_DELIVERY=07:00
TIMEZONE=America/New_York

# --- Runtime ---
DRY_RUN=False                       # skip push/PR creation
MAX_WORKERS=3                       # concurrent repo workers (1-16)
LOG_LEVEL=INFO
LOG_FORMAT=text                     # text or json
```

<br>

## LLM Providers

<details>
<summary><strong>Ollama (Local)</strong> &mdash; Self-hosted, fully private</summary>

<br>

```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama
```

Best for development and environments where code cannot leave the network.

</details>

<details>
<summary><strong>Azure AI Studios</strong> &mdash; Enterprise-grade</summary>

<br>

```bash
LLM_PROVIDER=azure
AZURE_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_API_KEY=your-key
AZURE_DEPLOYMENT_NAME=gpt-4
```

</details>

<details>
<summary><strong>NanoGPT</strong> &mdash; Lightweight API</summary>

<br>

```bash
LLM_PROVIDER=nanogpt
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=chatgpt-4o-latest
```

</details>

<br>

## Failure Handling

LucidPulls is built to fail gracefully. No single failure crashes the service or blocks other repositories.

<details>
<summary><strong>View full failure behavior table</strong></summary>

<br>

| Scenario | Behavior | Recovery |
|:---|:---|:---|
| **LLM unavailable** | Analysis returns empty; logged as "No actionable fixes identified" | Retries on next scheduled run |
| **GitHub rate limit** | Proactive quota check pauses until reset (+5s buffer), retries 3x with backoff | Automatic; remaining repos process after cooldown |
| **SSH key invalid** | Clone/pull fails for affected repo; other repos continue | Fix `SSH_KEY_PATH` in `.env` |
| **GitHub token invalid** | PR creation fails; repos still clone via SSH | Update `GITHUB_TOKEN` in `.env` |
| **Bad JSON from LLM** | Tries markdown fences, raw JSON, and nested extraction | Automatic skip on failure |
| **Fix fails syntax check** | Validated via `ast.parse()` (Python) or `node --check` (JS/TS); invalid fixes rejected | Automatic; no broken code committed |
| **Ambiguous match** | Fix rejected if original code appears more than once | Automatic |
| **Webhook missing** | Notification fails; review process unaffected | Set webhook URL, verify with `--test-notifications` |
| **Existing PR open** | Repo skipped to prevent duplicates | Merge or close existing LucidPulls PR |
| **Deadline reached** | Current repo finishes; remaining repos skipped | Adjust `SCHEDULE_DEADLINE` if needed |
| **SIGINT / SIGTERM** | Waits up to 60s for in-flight work, then shuts down | Graceful shutdown |
| **Database error** | Logged; review continues with potentially incomplete history | Check `data/` permissions |
| **All repos fail** | Failure alert sent via notification channel | Investigate LLM/repo issues |

</details>

<br>

## Project Structure

```
lucidpulls/
├── src/
│   ├── main.py              # Entry point & orchestration
│   ├── scheduler.py         # APScheduler job management
│   ├── config.py            # Pydantic settings & validation
│   ├── utils.py             # Retry logic, sanitization, parsing
│   ├── analyzers/           # Code & issue analysis
│   ├── llm/                 # LLM provider abstraction
│   ├── git/                 # Clone, branch, commit, PR creation
│   ├── notifications/       # Discord & Teams delivery
│   └── database/            # SQLAlchemy models & review history
├── tests/                   # pytest suite
├── data/                    # SQLite database (auto-created)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

<br>

## Testing

```bash
pytest                              # run all tests
pytest --cov=src --cov-report=html  # generate coverage report
pytest tests/test_analyzers.py      # run a specific test file
```

To verify your notification webhook is configured correctly:

```bash
python -m src.main --test-notifications
```

This sends a sample report with dummy data. No repositories are cloned and no PRs are created.

<br>

---

<p align="center">
  <sub>MIT License &bull; Built by <a href="https://github.com/YourBr0ther">YourBr0ther</a></sub>
</p>
