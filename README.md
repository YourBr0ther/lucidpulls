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
  <a href="#features">Features</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#quick-start">Quick Start</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#how-it-works">How It Works</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#configuration">Configuration</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#safety">Safety</a>&nbsp;&nbsp;&bull;&nbsp;&nbsp;<a href="#llm-providers">LLM Providers</a>
</p>

---

## Why LucidPulls?

Most bugs aren't hard to fix — they're hard to *find*. Null checks, unclosed resources, off-by-one errors, inverted conditions. They hide in plain sight and surface at the worst possible time.

LucidPulls runs overnight, reviews your repos with an LLM, and opens pull requests for conservative, high-confidence fixes. You review the PRs over coffee. That's it.

## Features

| | Feature | Description |
|:---:|:---|:---|
| **Scheduled** | Nightly Review | Configurable start time, deadline, and report delivery window |
| **Analysis** | Smart File Selection | Priority-scored file ranking — entry points first, tests last |
| **Validation** | Multi-Language Syntax Checks | Python, JavaScript, TypeScript, Go, Java, and Rust |
| **Fixes** | Automated PRs | Branch, commit, push, and pull request for every high-confidence fix |
| **Testing** | Post-Fix Test Execution | Optionally runs the repo's test suite after applying a fix; reverts on failure |
| **Memory** | Rejected Fix Tracking | Remembers fixes that failed and never re-suggests them |
| **Flexible** | Multiple LLM Backends | Azure AI Studios, NanoGPT, or Ollama (fully local / private) |
| **Reports** | Morning Notifications | PRs created, token usage, and run duration via Discord or Teams |
| **Resilient** | Failure Alerting | Sends a notification when a run produces zero PRs across all repos |
| **Safe** | Dry-Run Mode | Full pipeline minus push/PR creation for safe testing |
| **Managed** | Disk & Rate Limits | Configurable clone disk cap and proactive GitHub API quota management |
| **Observable** | Structured Logging | Text or JSON log output with per-run correlation IDs |

## Quick Start

### Prerequisites

- Python 3.11+
- GitHub Personal Access Token (repo scope)
- SSH key configured for GitHub
- Docker *(optional, for containerized deployment)*

### Install

```bash
git clone git@github.com:YourBr0ther/lucidpulls.git
cd lucidpulls
cp .env.example .env       # configure your settings
pip install -e ".[dev]"
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
  │  5. Run repo tests (optional)        │    └──────────────────────┘
  │  6. Create branch, commit, push      │
  │  7. Open pull request                │
  │                                      │
  └──────────────────────────────────────┘
```

### Review Pipeline

1. **Clone or pull** — Shallow clone for new repos, fast-forward pull for existing ones
2. **Check for duplicates** — Skip repos with an existing open LucidPulls PR
3. **Fetch issues** — Open bugs and enhancements guide LLM analysis
4. **Analyze code** — Files are priority-scored and sent to the LLM for review
5. **Filter fixes** — Only high-confidence fixes with a single exact match proceed
6. **Check memory** — Reject fixes that previously failed (hash-based deduplication)
7. **Apply and validate** — Write to temp file, syntax-check, atomically replace
8. **Run tests** — Optionally execute the repo's test suite; revert on failure
9. **Create PR** — Branch, commit, push, open pull request with structured body
10. **Record outcome** — Every result (success or failure) is stored in the database

## Safety

LucidPulls is designed to be conservative. It will never merge code — only open PRs for human review.

| Guardrail | Description |
|:---|:---|
| **High-confidence only** | Medium and low confidence fixes are silently skipped |
| **Single exact match** | Fix rejected if the original code appears more than once in the file |
| **Diff size limits** | Rejects fixes exceeding 200 lines or 3x code growth |
| **Syntax validation** | Python (`ast.parse`), JS (`node --check`), TS (`npx tsc`), Go (`go vet`), Java (`javac`), Rust (`rustc`/`cargo check`) |
| **Path traversal protection** | Null-byte, `..`, and absolute path detection in both fix application and commit |
| **Rejected fix memory** | Failed fixes are hashed and stored — never re-suggested |
| **Post-fix test execution** | Optionally runs repo tests after applying a fix; reverts and records rejection on failure |
| **One PR at a time** | Repos with an existing open LucidPulls PR are skipped |
| **Rate limiting** | Proactive GitHub API quota checks; raises error at 0 remaining instead of hitting 403s |

## Configuration

Create a `.env` file (or copy `.env.example`):

```bash
# --- Repositories ---
REPOS=owner/repo1,owner/repo2

# --- GitHub ---
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
GITHUB_USERNAME=YourUsername
GITHUB_EMAIL=your@email.com
SSH_KEY_PATH=~/.ssh/id_rsa

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
DRY_RUN=False
RUN_TESTS=True
TEST_TIMEOUT=120
MAX_WORKERS=3
LOG_LEVEL=INFO
LOG_FORMAT=text
```

### All Settings

| Variable | Required | Default | Description |
|:---|:---:|:---:|:---|
| `REPOS` | Yes | — | Comma-separated `owner/repo` list |
| `GITHUB_TOKEN` | Yes | — | Personal Access Token for API operations |
| `GITHUB_USERNAME` | Yes | — | Git commit author name |
| `GITHUB_EMAIL` | Yes | — | Git commit author email |
| `SSH_KEY_PATH` | No | `~/.ssh/id_rsa` | SSH private key for git clone/push |
| `LLM_PROVIDER` | No | `ollama` | `ollama`, `azure`, or `nanogpt` |
| `NOTIFICATION_CHANNEL` | No | `discord` | `discord` or `teams` |
| `DRY_RUN` | No | `False` | Run full pipeline but skip push and PR creation |
| `RUN_TESTS` | No | `True` | Run repo test suite after applying a fix |
| `TEST_TIMEOUT` | No | `120` | Max seconds for test execution (10–600) |
| `MAX_WORKERS` | No | `3` | Concurrent repo processing threads (1–16) |
| `SCHEDULE_START` | No | `02:00` | Nightly review start time (HH:MM) |
| `SCHEDULE_DEADLINE` | No | `06:00` | Stop submitting new repos after this time |
| `REPORT_DELIVERY` | No | `07:00` | Morning report delivery time |
| `TIMEZONE` | No | `America/New_York` | IANA timezone for all scheduling |
| `CLONE_DIR` | No | `/tmp/lucidpulls/repos` | Directory for cloned repositories |
| `MAX_CLONE_DISK_MB` | No | `5000` | Max disk usage for clones in MB (0 = unlimited) |
| `DB_BACKUP_ENABLED` | No | `True` | Auto-backup database before each run |
| `DB_BACKUP_COUNT` | No | `7` | Number of backup files to retain |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity |
| `LOG_FORMAT` | No | `text` | `text` for human-readable, `json` for structured |

## LLM Providers

<details>
<summary><strong>Ollama (Local)</strong> — Self-hosted, fully private</summary>

```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama
```

Best for development and environments where code cannot leave the network.

</details>

<details>
<summary><strong>Azure AI Studios</strong> — Enterprise-grade</summary>

```bash
LLM_PROVIDER=azure
AZURE_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_API_KEY=your-key
AZURE_DEPLOYMENT_NAME=gpt-4
```

</details>

<details>
<summary><strong>NanoGPT</strong> — Lightweight API</summary>

```bash
LLM_PROVIDER=nanogpt
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=chatgpt-4o-latest
```

</details>

## Failure Handling

LucidPulls is built to fail gracefully. No single failure crashes the service or blocks other repositories.

<details>
<summary><strong>View full failure behavior table</strong></summary>

| Scenario | Behavior | Recovery |
|:---|:---|:---|
| LLM unavailable | Returns empty analysis; logged as skipped | Retries on next scheduled run |
| GitHub rate limit exhausted | Raises `RateLimitExhausted`; repo skipped | Automatic; remaining repos continue |
| SSH key invalid | Clone/pull fails for affected repo | Fix `SSH_KEY_PATH` in `.env` |
| GitHub token invalid | Startup validation fails with clear error | Update `GITHUB_TOKEN` in `.env` |
| Bad JSON from LLM | Tries brace-matching extraction, newline escaping | Automatic skip on failure |
| Fix fails syntax check | Temp file validated before replacing original | Automatic; no broken code committed |
| Ambiguous match (>1 occurrence) | Fix rejected | Automatic |
| Fix too large | Rejected if >200 lines or >3x growth | Automatic |
| Tests fail after fix | Fix reverted, recorded in rejected fix memory | Never re-suggested |
| Webhook missing | Notification fails; review process unaffected | Set URL, verify with `--test-notifications` |
| Existing LucidPulls PR open | Repo skipped | Merge or close existing PR |
| Deadline reached | Current repo finishes; remaining repos skipped | Adjust `SCHEDULE_DEADLINE` |
| SIGINT / SIGTERM | Waits up to 60s for all in-flight workers | Graceful shutdown |
| Database migration fails | Clear error message with path to database | Check for corruption |
| All repos fail (0 PRs) | Failure alert sent via notification channel | Investigate LLM / repo issues |

</details>

## Project Structure

```
lucidpulls/
├── src/
│   ├── main.py              # Entry point & orchestrator
│   ├── config.py            # Pydantic settings & validation
│   ├── models.py            # Shared data models
│   ├── scheduler.py         # APScheduler job management
│   ├── utils.py             # Retry logic, sanitization, parsing
│   ├── __init__.py          # Logging setup, run ID context var
│   ├── analyzers/
│   │   ├── base.py          # BaseAnalyzer ABC, file scoring
│   │   ├── code_analyzer.py # LLM-powered analysis & fix application
│   │   └── issue_analyzer.py# Issue filtering & prioritization
│   ├── llm/
│   │   ├── base.py          # BaseLLM ABC, prompt templates
│   │   ├── ollama.py        # Ollama provider
│   │   ├── azure.py         # Azure AI Studios provider
│   │   └── nanogpt.py       # NanoGPT provider
│   ├── git/
│   │   ├── repo_manager.py  # Clone, pull, branch, commit, push
│   │   ├── pr_creator.py    # PR creation, label management
│   │   └── rate_limiter.py  # GitHub API quota management
│   ├── notifications/
│   │   ├── base.py          # BaseNotifier ABC
│   │   ├── discord.py       # Discord webhook delivery
│   │   └── teams.py         # Teams Adaptive Card delivery
│   └── database/
│       ├── models.py        # SQLAlchemy models
│       └── history.py       # Run tracking, backups, rejected fixes
├── tests/                   # 314 tests, 80% coverage
├── migrations/              # Alembic migrations
├── data/                    # SQLite database (auto-created)
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

## Testing

```bash
pytest                              # run all tests with coverage
pytest tests/test_analyzers.py      # run a specific test file
pytest -k "test_apply_fix"          # run tests matching a pattern
```

All tests use mocks for external I/O (GitHub API, LLM calls, git operations). No real credentials needed.

To verify your notification webhook:

```bash
python -m src.main --test-notifications
```

This sends a sample report with dummy data. No repositories are cloned and no PRs are created.

## Database

SQLite at `data/lucidpulls.db` with three tables:

| Table | Purpose |
|:---|:---|
| `review_runs` | Tracks each nightly run (start/end time, status, repo/PR counts) |
| `pr_records` | Individual PR outcomes per repo per run (success, error, tokens, timing) |
| `rejected_fixes` | Hash-indexed memory of fixes that failed — prevents re-suggestion |

WAL mode is enabled for crash recovery. A 5-second busy timeout prevents write contention failures during concurrent processing. Automatic backups are created before each run with configurable rotation.

Schema is managed by Alembic. Migrations run automatically on startup.

---

<p align="center">
  <sub>MIT License &bull; Built by <a href="https://github.com/YourBr0ther">YourBr0ther</a></sub>
</p>
