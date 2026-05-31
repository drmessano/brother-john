"""
Fetch Bible passage text from api.bible (scripture.api.bible).
Free tier: 5000 requests/day. Register at https://scripture.api.bible/
Falls back to a simple KJV public-domain endpoint if no API key is set.
"""
import os
import aiohttp

# Bible IDs on api.bible
BIBLE_IDS = {
    "ESV": "f421fe261da7624f-01",
    "NIV": "78a9f6124f344018-01",
    "KJV": "de4e12af7f28f599-02",
}

API_BASE = "https://api.scripture.api.bible/v1"


async def fetch_passage(reference: str, translation: str = "ESV") -> dict:
    """
    Returns {"text": str, "reference": str, "translation": str}
    reference: e.g. "John 3:16" or "Romans 8:28-30"
    """
    api_key = os.getenv("BIBLE_API_KEY", "")
    bible_id = BIBLE_IDS.get(translation, BIBLE_IDS["ESV"])

    if api_key:
        return await _fetch_from_api(reference, bible_id, api_key, translation)
    else:
        # Fallback: use a free no-key endpoint (KJV only via labs.bible.org)
        return await _fetch_fallback(reference, translation)


async def _fetch_from_api(reference: str, bible_id: str, api_key: str, translation: str) -> dict:
    search_url = f"{API_BASE}/bibles/{bible_id}/search"
    headers = {"api-key": api_key}
    params = {"query": reference, "limit": 1}

    async with aiohttp.ClientSession() as session:
        async with session.get(search_url, headers=headers, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()

    passages = data.get("data", {}).get("passages", [])
    if passages:
        raw = passages[0].get("content", "")
        text = _strip_html(raw)
        ref = passages[0].get("reference", reference)
    else:
        verses = data.get("data", {}).get("verses", [])
        if not verses:
            raise ValueError(f"No results found for '{reference}'")
        text = " ".join(_strip_html(v.get("text", "")) for v in verses)
        ref = f"{verses[0].get('reference', '')}–{verses[-1].get('reference', '')}" if len(verses) > 1 else verses[0].get("reference", reference)

    return {"text": text.strip(), "reference": ref, "translation": translation}


async def _fetch_fallback(reference: str, translation: str) -> dict:
    """labs.bible.org returns KJV JSON without an API key."""
    url = "https://labs.bible.org/api/"
    params = {"passage": reference, "type": "json"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Fallback Bible API error {resp.status}")
            data = await resp.json(content_type=None)

    if not data or not isinstance(data, list):
        raise ValueError(f"No results for '{reference}'")

    verses = []
    for v in data:
        verses.append(v.get("text", "").strip())

    first = data[0]
    last = data[-1]
    book = first.get("bookname", "")
    ch = first.get("chapter", "")
    v_start = first.get("verse", "")
    v_end = last.get("verse", "")
    ref = f"{book} {ch}:{v_start}" if v_start == v_end else f"{book} {ch}:{v_start}-{v_end}"

    return {"text": " ".join(verses), "reference": ref, "translation": "KJV (fallback)"}


def _strip_html(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html)
