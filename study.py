"""
Generate Bible study prompts using the Claude API.
"""
import os
import anthropic

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


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

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    return message.content[0].text


def generate_daily_passage_and_study(translation: str = "ESV") -> tuple[str, str]:
    """
    Ask Claude to choose a passage for today's study, then generate the study.
    Returns (reference, study_text).
    """
    system = (
        "You are a Bible study curator. Choose one Bible passage (3-10 verses) "
        "that would make an excellent focused daily study — something with depth, "
        "narrative, or a clear teaching moment. Vary the selection across different "
        "books and themes over time. Reply with ONLY the reference, e.g. 'Romans 8:28-39'."
    )

    selection = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=50,
        system=system,
        messages=[{"role": "user", "content": "Choose today's study passage."}],
    )

    reference = selection.content[0].text.strip()
    return reference


def generate_study_from_reference(reference: str, passage_text: str, translation: str) -> str:
    return generate_study_prompt(passage_text, reference, translation)
