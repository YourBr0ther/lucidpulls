# LucidPulls - Claude Code Project Guide

## What is this?

LucidPulls is an automated nightly code review agent. It runs on a schedule (default 2:00 AM), clones configured repositories, uses an LLM to detect high-confidence bugs, creates PRs with fixes, and sends morning summary reports via Discord or Teams.

Tagline: "Wake up to bug fixes, not bug reports."

## Tech Stack

- **Python 3.11+** with type hints throughout
- **Pydantic / pydantic-settings** for config validation (loaded from `.env`)
- **SQLAlchemy 2.0** ORM with **Alembic** migrations (SQLite backend, WAL mode)
- **GitPython** for git operations, **PyGithub** for GitHub API
- **APScheduler 3.x** for cron-like scheduling (staying on 3.x, not 4.x)
- **httpx** for HTTP clients (LLM providers, webhooks)
- **pytest** with pytest-cov for testing, **ruff** for linting, **mypy** for type checking

## Project Structure

```
src/
  main.py              # Entry point, LucidPulls orchestrator class, CLI args
  config.py            # Pydantic Settings (all env vars defined here)
  models.py            # Shared data models (GithubIssue, PRSummary, ReviewReport)
  scheduler.py         # APScheduler wrapper, DeadlineEnforcer, heartbeat
  utils.py             # retry decorator, sanitize_branch_name, parse_time_string
  __init__.py          # setup_logging(), current_run_id context var

  analyzers/
    base.py            # BaseAnalyzer ABC, FixSuggestion, AnalysisResult dataclasses
    code_analyzer.py   # CodeAnalyzer - LLM-powered bug detection and fix application
    issue_analyzer.py  # IssueAnalyzer - filters/prioritizes GitHub issues

  git/
    repo_manager.py    # RepoManager - clone, pull, branch, commit, push, cleanup
    pr_creator.py      # PRCreator - GitHub PR creation, open-PR checks
    rate_limiter.py    # GitHubRateLimiter - proactive rate limit management

  llm/
    base.py            # BaseLLM ABC, BaseHTTPLLM, LLMResponse, prompt templates
    ollama.py          # OllamaLLM provider
    azure.py           # AzureLLM provider
    nanogpt.py         # NanoGPTLLM provider
    __init__.py        # get_llm() factory function

  notifications/
    base.py            # BaseNotifier ABC, NotificationResult
    discord.py         # DiscordNotifier
    teams.py           # TeamsNotifier
    __init__.py        # get_notifier() factory function

  database/
    history.py         # ReviewHistory - run tracking, PR records, reports, backups
    models.py          # SQLAlchemy models: ReviewRun, PRRecord

tests/                 # 12 test files, 259 tests, 81% coverage
migrations/            # Alembic migrations (0001_initial_schema, 0002_add_indexes)
```

## Key Architecture Patterns

- **Single orchestrator**: `LucidPulls` class in `main.py` owns all components and coordinates the review pipeline
- **Shared GitHub client**: One `Github()` instance shared across RepoManager, PRCreator, and RateLimiter
- **Factory functions**: `get_llm()` and `get_notifier()` create providers based on config
- **Context manager pattern**: Most components implement `close()` and `__enter__`/`__exit__`
- **ThreadPoolExecutor**: Repos processed concurrently with configurable `max_workers`
- **Graceful shutdown**: Signal handlers set `_shutdown` flag, `_idle` event gates cleanup, 60s drain timeout
- **Run correlation**: `contextvars.ContextVar` propagates `run_id` through threads for log correlation
- **LLM token tracking**: Flows from LLMResponse → AnalysisResult → record_pr → build_report → notifications
- **Failure alerting**: Sends notification when all repos in a run fail (0 PRs created)
- **File priority scoring**: Entry points and core files analyzed first; tests, examples, migrations last

## Review Pipeline Flow

1. `run_review()` starts a DB run record, iterates repos with ThreadPoolExecutor
2. `_process_repo()` clones/pulls the repo, then calls `_analyze_and_fix()`
3. `_analyze_and_fix()` checks for existing LucidPulls PR, fetches issues, runs LLM analysis
4. `CodeAnalyzer.analyze()` collects code files, sends to LLM, parses JSON response
5. Only HIGH confidence fixes proceed; ambiguous matches (>1 occurrence) are rejected
6. Fix is applied to temp file, syntax-validated, then atomically replaced
7. Branch created, committed, pushed, PR created via GitHub API
8. Every outcome (success or failure) is recorded in the database

## Safety Constraints (Do Not Weaken)

- **High-confidence only**: Medium/low confidence fixes are silently skipped
- **Single exact match**: If `original_code` appears more than once in the file, the fix is rejected
- **Syntax validation**: Python (ast.parse), JS (node --check), TS (npx tsc) before committing
- **Path traversal checks**: Both in `apply_fix()` and `commit_changes()`
- **One PR at a time**: Skips repos that already have an open LucidPulls PR
- **Rate limiting**: Proactive GitHub API quota checks before operations
- **Dry-run mode**: Full pipeline minus push/PR creation for safe testing

## Running the App

```bash
# Install
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Run modes
python -m src.main                  # Start scheduled service
python -m src.main --run-now        # Immediate review
python -m src.main --dry-run        # Analyze without pushing
python -m src.main --run-now --dry-run  # Immediate dry run (best for testing)
python -m src.main --send-report    # Send report for latest run
python -m src.main --test-notifications  # Test webhook delivery
python -m src.main --health-check   # Docker health check
python -m src.main --debug          # Debug logging
```

## Running Tests

```bash
pytest                              # Full suite with coverage
pytest tests/test_config.py         # Single module
pytest -k "test_analyze"            # By name pattern
```

All tests use mocks for external I/O (GitHub API, LLM, git). No real credentials needed.

## Configuration

All config is in `src/config.py` as a Pydantic `Settings` class. Env vars are loaded from `.env`. Key settings:

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `REPOS` | Yes | - | Comma-separated `owner/repo` list |
| `GITHUB_TOKEN` | Yes | - | PAT for API operations |
| `GITHUB_USERNAME` | Yes | - | For git commits |
| `GITHUB_EMAIL` | Yes | - | For git commits |
| `LLM_PROVIDER` | No | `ollama` | `ollama`, `azure`, or `nanogpt` |
| `NOTIFICATION_CHANNEL` | No | `discord` | `discord` or `teams` |
| `DRY_RUN` | No | `False` | Skip push/PR creation |
| `MAX_WORKERS` | No | `3` | Concurrent repo workers (1-16) |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `LOG_FORMAT` | No | `text` | `text` or `json` |
| `CLONE_DIR` | No | `/tmp/lucidpulls/repos` | Directory for cloned repos |
| `MAX_CLONE_DISK_MB` | No | `5000` | Max disk usage for clones in MB (0 = unlimited) |
| `DB_BACKUP_ENABLED` | No | `True` | Auto-backup DB before each run |
| `DB_BACKUP_COUNT` | No | `7` | Number of recent backups to keep |

## Database

SQLite at `data/lucidpulls.db`. Two tables: `review_runs` and `pr_records`. Managed by Alembic migrations in `migrations/`. WAL mode enabled for crash recovery. Automatic backups before each run.

## Common Development Tasks

- **Add a new LLM provider**: Subclass `BaseLLM` (or `BaseHTTPLLM`) in `src/llm/`, add to `get_llm()` factory in `__init__.py`, add config fields to `Settings`, add to `validate_llm_provider_config()`
- **Add a notification channel**: Subclass `BaseNotifier` in `src/notifications/`, add to `get_notifier()` factory, add config fields to `Settings`, add to `validate_notification_config()`
- **Add a database migration**: Use Alembic: `alembic revision --autogenerate -m "description"`, then review the generated migration
- **Change analysis behavior**: Modify prompts in `src/llm/base.py` (CODE_REVIEW_SYSTEM_PROMPT, FIX_GENERATION_PROMPT_TEMPLATE) or filtering logic in `CodeAnalyzer._parse_llm_response()`

## Known Limitations

- One fix per repo per night (by design)
- 50 files max, 100KB per file, 50KB total sent to LLM
- File selection is priority-scored (entry points, core files first; tests, examples last)
- JS validation requires Node.js; TS validation skips if tsc unavailable
- Only supports GitHub (no GitLab/Gitea)
