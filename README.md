# BibleBot

A Telegram bot for focused daily Bible study, powered by Claude.

## Setup

### 1. Get your API keys

- **Telegram Bot Token** — message [@BotFather](https://t.me/BotFather) on Telegram, create a new bot with `/newbot`
- **Anthropic API Key** — from [console.anthropic.com](https://console.anthropic.com)
- **Bible API Key** (optional but recommended) — free at [scripture.api.bible](https://scripture.api.bible/). Without it, the bot falls back to KJV only via a public endpoint.

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your keys
```

### 3. Install & run

```bash
pip install -r requirements.txt
python bot.py
```

## Commands

| Command | Description |
|---------|-------------|
| `/study` | Generate a fresh Bible study passage (Claude chooses the passage) |
| `/verse John 3:16` | Deep-dive study on any specific passage |
| `/daily 8:00` | Receive a daily study at 8:00 AM UTC |
| `/daily off` | Stop daily studies |
| `/translation` | Switch between ESV, NIV, KJV |
| `/settings` | View your current preferences |

## What a study looks like

Each study includes:
- **The passage text** in your chosen translation
- **Purpose** — why this passage matters
- **Key Insight** — a rich theological or historical observation
- **3 Reflection Questions** — for personal engagement
- **A closing prayer or thought**
