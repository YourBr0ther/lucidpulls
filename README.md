<p align="center">
  <img src="assets/logo.svg" alt="LucidPulls" width="140" height="140">
</p>

<h1 align="center">LucidPulls</h1>

<p align="center">
  <strong>Code review for bugs while you sleep.</strong>
</p>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#how-it-works">How It Works</a> •
  <a href="#configuration">Configuration</a> •
  <a href="#llm-providers">LLM Providers</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python 3.11+">
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT License">
  <img src="https://img.shields.io/badge/docker-ready-2496ed?style=flat-square&logo=docker&logoColor=white" alt="Docker Ready">
</p>

---

LucidPulls is an automated nightly code review agent that analyzes your GitHub repositories, identifies bugs and improvement opportunities, creates pull requests with fixes, and delivers a morning summary report.

Wake up to bug fixes, not bug reports.

## Features

| | Feature | Description |
|:---:|:---|:---|
| :crescent_moon: | **Scheduled Nightly Review** | Configurable start time, deadline, and report delivery |
| :mag: | **Repository Analysis** | Clones/pulls latest code, analyzes for bugs, reviews open issues |
| :arrows_counterclockwise: | **Automated PR Creation** | Creates pull requests with conservative, high-confidence fixes |
| :robot: | **Multiple LLM Backends** | Azure AI Studios, NanoGPT, or Ollama (local) |
| :bell: | **Notifications** | Morning reports via Discord or Microsoft Teams |

## Quick Start

### Prerequisites

- Python 3.11+
- Docker and docker-compose (for containerized deployment)
- GitHub Personal Access Token
- SSH key configured for GitHub

### Installation

```bash
# Clone the repository
git clone git@github.com:YourBr0ther/lucidpulls.git
cd lucidpulls

# Copy and configure environment
cp .env.example .env

# Install dependencies
pip install -r requirements.txt
```

### Running

```bash
# Run as a service (scheduled)
python -m src.main

# Run immediately (for testing)
python -m src.main --run-now

# Send report only
python -m src.main --send-report

# Debug mode
python -m src.main --debug
```

### Docker Deployment

```bash
docker-compose up -d        # Build and run
docker-compose logs -f      # View logs
docker-compose down         # Stop
```

## How It Works

```
                    ┌─────────────────────────────────────────────┐
                    │              NIGHTLY SCHEDULE                │
                    │                  02:00 AM                    │
                    └─────────────────────┬───────────────────────┘
                                          │
                    ┌─────────────────────▼───────────────────────┐
                    │           FOR EACH REPOSITORY               │
                    │  ┌────────────────────────────────────────┐ │
                    │  │  1. Clone or pull latest code          │ │
                    │  │  2. Fetch open issues (bugs/enhance)   │ │
                    │  │  3. Send to LLM for analysis           │ │
                    │  │  4. Identify high-confidence fix       │ │
                    │  │  5. Create branch, commit, push        │ │
                    │  │  6. Open pull request                  │ │
                    │  └────────────────────────────────────────┘ │
                    └─────────────────────┬───────────────────────┘
                                          │
                    ┌─────────────────────▼───────────────────────┐
                    │              MORNING REPORT                  │
                    │       Summary sent to Discord/Teams          │
                    │                  07:00 AM                    │
                    └─────────────────────────────────────────────┘
```

## Fix Types

LucidPulls focuses on conservative, high-confidence fixes:

- **Null checks** — Missing null/None checks
- **Error handling** — Uncaught exceptions and error gaps
- **Off-by-one errors** — Array bounds and loop conditions
- **Logic typos** — Wrong operators, inverted conditions
- **Resource leaks** — Unclosed files and connections
- **Security issues** — Obvious vulnerabilities

## Configuration

Edit `.env` with your settings:

```bash
# Repositories to review (comma-separated)
REPOS=owner/repo1,owner/repo2

# GitHub Authentication
GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
GITHUB_USERNAME=YourUsername
GITHUB_EMAIL=your@email.com

# LLM Provider (azure|nanogpt|ollama)
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama

# Notification Channel (discord|teams)
NOTIFICATION_CHANNEL=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Schedule (24-hour format)
SCHEDULE_START=02:00
SCHEDULE_DEADLINE=06:00
REPORT_DELIVERY=07:00
TIMEZONE=America/New_York
```

## LLM Providers

<details>
<summary><strong>Ollama (Local)</strong> — Best for development and self-hosted deployments</summary>

```bash
LLM_PROVIDER=ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama
```
</details>

<details>
<summary><strong>Azure AI Studios</strong> — For enterprise environments</summary>

```bash
LLM_PROVIDER=azure
AZURE_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_API_KEY=your-key
AZURE_DEPLOYMENT_NAME=gpt-4
```
</details>

<details>
<summary><strong>NanoGPT</strong> — Lightweight API option</summary>

```bash
LLM_PROVIDER=nanogpt
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=chatgpt-4o-latest
```
</details>

## Project Structure

```
lucidpulls/
├── src/
│   ├── main.py              # Entry point
│   ├── scheduler.py         # Job scheduling
│   ├── config.py            # Configuration
│   ├── analyzers/           # Code & issue analysis
│   ├── llm/                 # LLM providers
│   ├── git/                 # Git operations
│   ├── notifications/       # Discord/Teams
│   └── database/            # Review history
├── tests/                   # Unit tests
├── data/                    # SQLite database
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Testing

```bash
pytest                              # Run all tests
pytest --cov=src --cov-report=html  # Run with coverage
pytest tests/test_analyzers.py     # Run specific test file
```

---

<p align="center">
  <sub>MIT License</sub>
</p>
