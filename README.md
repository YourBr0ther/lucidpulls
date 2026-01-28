# LucidPulls

> Code review for bugs while you sleep.

LucidPulls is an automated nightly code review agent that analyzes your GitHub repositories, identifies bugs and improvement opportunities, creates pull requests with fixes, and delivers a morning summary report.

## Features

- **Scheduled Nightly Review**: Configurable start time, deadline, and report delivery
- **Repository Analysis**: Clones/pulls latest code, analyzes for bugs, reviews open issues
- **Automated PR Creation**: Creates pull requests with conservative, high-confidence fixes
- **Multiple LLM Backends**: Azure AI Studios, NanoGPT, or Ollama (local)
- **Notifications**: Morning reports via Discord or Microsoft Teams

## Quick Start

### Prerequisites

- Python 3.11+
- Docker and docker-compose (for containerized deployment)
- GitHub Personal Access Token
- SSH key configured for GitHub

### Installation

1. Clone the repository:
   ```bash
   git clone git@github.com:YourBr0ther/lucidpulls.git
   cd lucidpulls
   ```

2. Copy and configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

### Configuration

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

### Running

**As a service:**
```bash
python -m src.main
```

**Run immediately (for testing):**
```bash
python -m src.main --run-now
```

**Send report only:**
```bash
python -m src.main --send-report
```

**Debug mode:**
```bash
python -m src.main --debug
```

### Docker Deployment

```bash
# Build and run
docker-compose up -d

# View logs
docker-compose logs -f

# Stop
docker-compose down
```

## How It Works

1. **Nightly at scheduled time**: LucidPulls starts reviewing configured repositories
2. **For each repository**:
   - Clone or pull latest code
   - Fetch open issues labeled as bugs/enhancements
   - Send code and issues to LLM for analysis
   - LLM identifies one high-confidence fix
   - Apply fix, create branch, commit, push
   - Open pull request
3. **Morning report**: Summary of all PRs created sent to Discord/Teams

## Fix Types

LucidPulls focuses on conservative, high-confidence fixes:

- Missing null/None checks
- Error handling gaps
- Off-by-one errors
- Logic typos (wrong operators, inverted conditions)
- Resource leaks (unclosed files, connections)
- Obvious security issues

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
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_analyzers.py
```

## LLM Providers

### Ollama (Local)
Best for development and self-hosted deployments:
```bash
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama
```

### Azure AI Studios
For enterprise environments:
```bash
AZURE_ENDPOINT=https://your-resource.openai.azure.com/
AZURE_API_KEY=your-key
AZURE_DEPLOYMENT_NAME=gpt-4
```

### NanoGPT
Lightweight API option:
```bash
NANOGPT_API_KEY=your-key
NANOGPT_MODEL=chatgpt-4o-latest
```

## License

MIT
