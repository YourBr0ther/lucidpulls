# LucidPulls - Claude Code Project Guide

## What Is This?

LucidPulls is an automated nightly code review agent. It runs on a configurable schedule (default 2:00 AM), clones configured GitHub repositories, uses an LLM to detect high-confidence bugs, creates pull requests with fixes, and sends morning summary reports via Discord or Microsoft Teams.

**Tagline:** "Wake up to bug fixes, not bug reports."

## Tech Stack

- **Python 3.11+** with type hints throughout
- **Pydantic / pydantic-settings** for config validation (loaded from `.env`)
- **SQLAlchemy 2.0** ORM with **Alembic** migrations (SQLite backend, WAL mode + busy_timeout)
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
    base.py            # BaseAnalyzer ABC, FixSuggestion, AnalysisResult, TestResult
    code_analyzer.py   # CodeAnalyzer - LLM-powered bug detection and fix application
    issue_analyzer.py  # IssueAnalyzer - filters/prioritizes GitHub issues

  git/
    repo_manager.py    # RepoManager - clone, pull, branch, commit, push, cleanup
    pr_creator.py      # PRCreator - GitHub PR creation, open-PR checks, labeling
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
    models.py          # SQLAlchemy models: ReviewRun, PRRecord, RejectedFix

tests/                 # 12 test files, 314 tests, 80% coverage
migrations/            # Alembic (0001 schema, 0002 indexes, 0003 bug_description, 0004 rejected_fixes)

k8s/                   # Kubernetes/k3s manifests
  namespace.yaml       # lucidpulls namespace
  secret.yaml          # GitHub token, SSH key, API keys
  configmap.yaml       # Non-secret configuration
  pvc.yaml             # SQLite data persistence (1Gi, local-path)
  deployment.yaml      # Single-replica deployment with health checks
  kustomization.yaml   # Kustomize entrypoint

Dockerfile             # Python 3.11-slim, non-root, SHA256-pinned
docker-compose.yml     # Single-host deployment with resource limits
entrypoint.sh          # Permission fixer + gosu drop to non-root
```

## Key Architecture Patterns

- **Single orchestrator**: `LucidPulls` class in `main.py` owns all components and coordinates the review pipeline
- **Shared GitHub client**: One `Github()` instance shared across RepoManager, PRCreator, and RateLimiter
- **Factory functions**: `get_llm()` and `get_notifier()` create providers based on config
- **Context manager pattern**: Most components implement `close()` and `__enter__`/`__exit__`
- **ThreadPoolExecutor**: Repos processed concurrently with configurable `max_workers`
- **Atomic worker tracking**: `_active_workers` counter with lock ensures shutdown waits for ALL concurrent workers, not just one
- **Graceful shutdown**: Signal handlers set flags, scheduler stops, main thread waits up to 60s for in-flight work
- **Run correlation**: `contextvars.ContextVar` propagates `run_id` through threads for log correlation
- **Thread-safe HTTP clients**: `BaseHTTPLLM` uses `threading.local()` for per-thread httpx clients, with centralized tracking for proper cleanup
- **LLM token tracking**: Flows from LLMResponse -> AnalysisResult -> record_pr -> build_report -> notifications
- **Failure alerting**: Sends notification when all repos in a run fail (0 PRs created)
- **File priority scoring**: Entry points and core files analyzed first; tests, examples, migrations last
- **Rejected fix memory**: Previously failed fixes are hashed and stored to prevent re-suggestion

## Review Pipeline Flow

1. `run_review()` starts a DB run record, iterates repos with ThreadPoolExecutor
2. `_process_repo()` clones/pulls the repo, then calls `_analyze_and_fix()`
3. `_analyze_and_fix()` checks for existing LucidPulls PR, fetches issues, runs LLM analysis
4. `CodeAnalyzer.analyze()` collects code files (priority-scored), sends to LLM, parses JSON response
5. Only HIGH confidence fixes proceed; ambiguous matches (>1 occurrence) are rejected
6. Fix is checked against rejected fix memory to avoid re-suggesting known failures
7. Diff size limits enforced (max 200 lines, max 3x growth factor)
8. Fix is applied to temp file, syntax-validated, then atomically replaced
9. Optional post-fix test execution catches regressions before committing
10. Branch created, committed, pushed, PR created via GitHub API
11. Every outcome (success or failure) is recorded in the database

## Safety Constraints (Do Not Weaken)

- **High-confidence only**: Medium/low confidence fixes are silently skipped
- **Single exact match**: If `original_code` appears more than once in the file, the fix is rejected
- **Diff size limits**: Fixes exceeding 200 lines or 3x growth factor are rejected
- **Syntax validation**: Python (`ast.parse`), JS (`node --check`), TS (`npx tsc`), Go (`go vet`), Java (`javac`), Rust (`rustc`/`cargo check`)
- **Path traversal checks**: Both in `apply_fix()` and `commit_changes()`, including null-byte and `..` detection
- **Rejected fix memory**: Fixes that fail to apply or break tests are recorded and never re-suggested
- **Post-fix test execution**: Configurable (`RUN_TESTS`); reverts fix and records rejection if tests fail
- **One PR at a time**: Skips repos that already have an open LucidPulls PR (label-based detection)
- **Rate limiting**: Proactive GitHub API quota checks before operations; raises `RateLimitExhausted` at 0 remaining
- **Dry-run mode**: Full pipeline minus push/PR creation for safe testing

## Running the App

```bash
# Install
pip install -e ".[dev]"

# Copy and configure environment
cp .env.example .env
# Edit .env with your settings

# Run modes
python -m src.main                      # Start scheduled service
python -m src.main --run-now            # Immediate review
python -m src.main --dry-run            # Analyze without pushing
python -m src.main --run-now --dry-run  # Immediate dry run (best for testing)
python -m src.main --send-report        # Send report for latest run
python -m src.main --test-notifications # Test webhook delivery
python -m src.main --health-check       # Docker health check
python -m src.main --debug              # Debug logging
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
|---|---|---|---|
| `REPOS` | Yes | - | Comma-separated `owner/repo` list |
| `GITHUB_TOKEN` | Yes | - | PAT for API operations |
| `GITHUB_USERNAME` | Yes | - | For git commits |
| `GITHUB_EMAIL` | Yes | - | For git commits |
| `LLM_PROVIDER` | No | `ollama` | `ollama`, `azure`, or `nanogpt` |
| `NOTIFICATION_CHANNEL` | No | `discord` | `discord` or `teams` |
| `DRY_RUN` | No | `False` | Skip push/PR creation |
| `RUN_TESTS` | No | `True` | Run repo tests after applying a fix |
| `TEST_TIMEOUT` | No | `120` | Seconds before test execution times out (10-600) |
| `MAX_WORKERS` | No | `3` | Concurrent repo workers (1-16) |
| `LOG_LEVEL` | No | `INFO` | Logging level |
| `LOG_FORMAT` | No | `text` | `text` or `json` |
| `CLONE_DIR` | No | `/tmp/lucidpulls/repos` | Directory for cloned repos |
| `MAX_CLONE_DISK_MB` | No | `5000` | Max disk usage for clones in MB (0 = unlimited) |
| `DB_BACKUP_ENABLED` | No | `True` | Auto-backup DB before each run |
| `DB_BACKUP_COUNT` | No | `7` | Number of recent backups to keep |

## Database

SQLite at `data/lucidpulls.db`. Three tables: `review_runs`, `pr_records`, and `rejected_fixes`. Managed by Alembic migrations in `migrations/`. WAL mode enabled for crash recovery, `busy_timeout=5000` for concurrent write safety. Automatic backups before each run with configurable rotation.

## Common Development Tasks

- **Add a new LLM provider**: Subclass `BaseLLM` (or `BaseHTTPLLM`) in `src/llm/`, add to `get_llm()` factory in `__init__.py`, add config fields to `Settings`, add to `validate_llm_provider_config()`
- **Add a notification channel**: Subclass `BaseNotifier` in `src/notifications/`, add to `get_notifier()` factory, add config fields to `Settings`, add to `validate_notification_config()`
- **Add a database migration**: Use Alembic: `alembic revision --autogenerate -m "description"`, then review the generated migration
- **Change analysis behavior**: Modify prompts in `src/llm/base.py` (`CODE_REVIEW_SYSTEM_PROMPT`, `FIX_GENERATION_PROMPT_TEMPLATE`) or filtering logic in `CodeAnalyzer._parse_llm_response()`
- **Add a syntax validator**: Add a `_validate_<lang>_syntax()` method to `CodeAnalyzer` and register it in `_validate_syntax()`

## Docker Deployment

```bash
# Build and run with docker compose
docker compose up -d

# Build and push to Docker Hub
docker build -t yourbr0ther/lucidpulls:latest .
docker push yourbr0ther/lucidpulls:latest

# Or use compose to build
docker compose build
docker compose push
```

## Kubernetes (k3s) Deployment

```bash
# Edit secrets and config first
vim k8s/secret.yaml      # Add GitHub token, SSH key, API keys
vim k8s/configmap.yaml   # Set repos, LLM provider, schedule

# Deploy with kustomize
kubectl apply -k k8s/

# Check status
kubectl -n lucidpulls get pods
kubectl -n lucidpulls logs -f deployment/lucidpulls
```

The deployment uses:
- Single replica (SQLite constraint) with `Recreate` strategy
- PVC on `local-path` storage class for database persistence
- Memory-backed emptyDir for temp clone directory
- SSH key mounted as Kubernetes secret
- Liveness probe via `--health-check`
- 90s termination grace period for graceful shutdown

## Known Limitations

- One fix per repo per night (by design)
- 50 files max, 100KB per file, 50KB total sent to LLM
- File selection is priority-scored (entry points, core files first; tests, examples last)
- JS validation requires Node.js (fail-closed); TS/Go/Java/Rust validation skips if tools unavailable (fail-open)
- Only supports GitHub (no GitLab/Bitbucket/Gitea)
- Shallow clones (`depth=1`) used for speed; may cause issues with some git server configurations on push
