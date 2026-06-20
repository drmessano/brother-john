"""
Generate Bible study prompts using the Claude API.
"""
import os
import time
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

CLAUDE_TIMEOUT = 30.0   # seconds per API call
RETRY_ATTEMPTS = 2
RETRY_DELAY = 3         # seconds between retries


def _with_retry(fn):
    """Call fn(), retrying once after RETRY_DELAY seconds on failure."""
    last_exc = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            if attempt < RETRY_ATTEMPTS - 1:
                time.sleep(RETRY_DELAY)
    raise last_exc


def generate_study_prompt(passage_text: str, reference: str, translation: str) -> str:
    """
    Returns a formatted study prompt (plain text, Telegram-safe markdown).
    """
    system = (
        "You are a warm, scholarly Bible study guide. "
        "When given a Bible passage, you provide:\n"
        "1. A brief PURPOSE — why this passage matters and what God is communicating\n"
        "2. KEY INSIGHT — one rich observation about the text (historical, linguistic, or theological)\n"
        "3. REFLECTION QUESTIONS — exactly 3 questions that help the reader personally engage with the passage\n"
        "4. A short PRAYER or closing thought (2-3 sentences)\n\n"
        "Format your response using these exact headers. Keep it warm and accessible, "
        "not overly academic. Assume the reader wants to grow, not just learn facts."
    )

    user = f"Passage ({translation}):\n\n{reference}\n\n{passage_text}"

    def _call():
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            timeout=CLAUDE_TIMEOUT,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return message.content[0].text

    return _with_retry(_call)


def generate_daily_passage_and_study(translation: str = "KJV", user_id: int = 0, deterministic: bool = True) -> str:
    """
    Ask Claude to choose a passage for today's study.
    Returns the reference string.
    deterministic=True (default): same book per user per day, used for /daily pre-cache.
    deterministic=False: random book each call, used for /study.
    """
    import random
    from datetime import date

    today = date.today().isoformat()

    books = [
        "Genesis", "Exodus", "Psalms", "Proverbs", "Isaiah", "Jeremiah",
        "Matthew", "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians",
        "2 Corinthians", "Galatians", "Ephesians", "Philippians", "Colossians",
        "1 Thessalonians", "Hebrews", "James", "1 Peter", "1 John", "Revelation",
    ]
    rng = random.Random(f"{user_id}-{today}") if deterministic else random.Random()
    suggested_book = rng.choice(books)

    system = (
        "You are a Bible study curator. Choose one Bible passage (3-10 verses) "
        "that would make an excellent focused daily study — something with depth, "
        "narrative, or a clear teaching moment. "
        "Reply with ONLY the reference, e.g. 'Romans 8:28-39'."
    )

    def _call():
        selection = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            timeout=CLAUDE_TIMEOUT,
            system=system,
            messages=[{"role": "user", "content": f"Choose a study passage from the book of {suggested_book} for {today}."}],
        )
        return selection.content[0].text.strip()

    return _with_retry(_call)


def generate_study_from_reference(reference: str, passage_text: str, translation: str) -> str:
    return generate_study_prompt(passage_text, reference, translation)
