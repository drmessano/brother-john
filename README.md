# Brother John

> *"Always ready with a passage, a reflection, and a question worth sitting with."*

Brother John is a Telegram bot that brings daily Bible study to your pocket. Powered by Claude, he doesn't just look up verses — he meets you in them. Each study comes with context, a key insight, reflection questions, and a closing prayer, whether you ask on a whim or set him to show up every morning.

---

## Installation

### Prerequisites

- Python 3.11+
- A Telegram account
- An Anthropic API key
- A Bible API key (optional, but unlocks ESV and NIV)

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
| `BIBLE_API_KEY` | Free at [scripture.api.bible](https://scripture.api.bible/) — unlocks ESV & NIV (KJV works without it) |

### 4. Configure

```bash
cp .env.example .env
# Open .env and fill in your keys
```

### 5. Run

```bash
python bot.py
```

Brother John is now online. Find him in Telegram and say `/start`.

---

## Commands

| Command | What Brother John does |
|---------|----------------------|
| `/study` | Chooses a passage and delivers a full study |
| `/verse Romans 8:28` | Deep-dive study on any passage you pick |
| `/daily 8:00` | Show up every morning at 8:00 AM UTC |
| `/daily off` | Take a break |
| `/translation` | Switch between ESV, NIV, and KJV |
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

## Running in the background

To keep Brother John running after you close your terminal:

```bash
nohup python bot.py &
```

Or use a process manager like `systemd`, `supervisord`, or `pm2`.

---

## License

MIT
