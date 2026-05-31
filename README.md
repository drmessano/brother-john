# Brother John

> *"Always ready with a passage, a reflection, and a question worth sitting with."*

Brother John is a Telegram bot that brings daily Bible study to your pocket. Powered by Claude, he doesn't just look up verses — he meets you in them. Each study comes with context, a key insight, reflection questions, and a closing prayer, whether you ask on a whim or set him to show up every morning.

---

## Installation

### Prerequisites

- Python 3.11+
- A Telegram account
- An Anthropic API key
- A Bible API key (optional — KJV works without one, but registering unlocks the full translation list)

### 1. Clone the repo

```bash
git clone https://github.com/drmessano/brother-john.git
cd brother-john
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get your API keys

| Key | Where to get it |
|-----|----------------|
| `TELEGRAM_BOT_TOKEN` | Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) |
| `BIBLE_API_KEY` | Free at [scripture.api.bible](https://scripture.api.bible/) — unlocks additional translations (KJV works without it) |

### 4. Configure

```bash
cp config.example config
# Edit config and fill in your keys
```

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Yes | From [@BotFather](https://t.me/BotFather) |
| `ANTHROPIC_API_KEY` | Yes | From [console.anthropic.com](https://console.anthropic.com) |
| `BIBLE_API_KEY` | No | From [scripture.api.bible](https://scripture.api.bible/) — KJV works without it |
| `LOG_DIR` | No | Log directory, defaults to `/var/log/brother-john` |

### 5. Run

```bash
./bot.py start
```

Brother John is now online and running as a background daemon. Find him in Telegram and say `/start`.

To stop him:

```bash
./bot.py stop
```

---

## Commands

| Command | What Brother John does |
|---------|----------------------|
| `/study` | Chooses a passage and delivers a full study |
| `/verse Romans 8:28` | Deep-dive study on any passage you pick |
| `/daily 8:00` | Show up every morning at 8:00 AM UTC |
| `/daily off` | Take a break |
| `/translation` | Switch Bible translation — list is pulled live from the API |
| `/settings` | Check your current preferences |

---

## What a study looks like

Every study includes:

- **The passage** — in your chosen translation
- **Purpose** — what God is communicating and why it matters
- **Key Insight** — one rich theological, historical, or linguistic observation
- **3 Reflection Questions** — for personal engagement, not just head knowledge
- **A closing prayer or thought** — to land the passage in your heart

---

## Logging

Logs are written to `/var/log/brother-john/brother-john.log` and rotated at 5 MB, keeping the 10 most recent files. Override the directory with `LOG_DIR` in your `.env`.

## PID file

The daemon writes its PID to `/var/run/brother-john.pid`. `./bot.py stop` uses this to send the shutdown signal. If the process dies unexpectedly, delete the stale PID file before running `start` again.

---

## License

MIT
