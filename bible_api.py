#!/usr/bin/env python3
import os
import aiohttp

API_BASE = "https://rest.api.bible/v1"

_bible_cache: dict | None = None


async def get_available_bibles() -> list[dict]:
    """Returns list of available bibles from api.bible. Results are cached."""
    global _bible_cache
    if _bible_cache is not None:
        return _bible_cache

    api_key = os.getenv("BIBLE_API_KEY", "")
    if not api_key:
        return []

    async with aiohttp.ClientSession() as session:
        async with session.get(f"{API_BASE}/bibles", headers={"api-key": api_key}) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()

    _bible_cache = data.get("data", [])
    return _bible_cache


def _normalize_abbr(abbr: str) -> str:
    """Strip language prefixes (eng, es, fr, etc.) and trailing digits.
    e.g. engKJV -> KJV, NIV11 -> NIV, engWEBU -> WEBU
    """
    import re
    abbr = re.sub(r"^[a-z]{2,3}(?=[A-Z])", "", abbr)   # strip lowercase prefix before uppercase
    abbr = re.sub(r"\d+$", "", abbr)                     # strip trailing digits
    return abbr.upper()


async def find_bible_id(abbreviation: str) -> str | None:
    """Look up a bible ID by abbreviation. Normalizes both sides so that
    e.g. 'KJV' matches 'engKJV' and 'NIV' matches 'NIV11'.
    Prefers exact match, then normalized match with shortest abbreviation.
    """
    bibles = await get_available_bibles()
    abbr_upper = abbreviation.upper()
    normalized = _normalize_abbr(abbreviation)

    # Exact match first
    for b in bibles:
        if b.get("abbreviation", "").upper() == abbr_upper:
            return b["id"]

    # Normalized match
    matches = [b for b in bibles if _normalize_abbr(b.get("abbreviation", "")) == normalized]
    if matches:
        matches.sort(key=lambda b: len(b.get("abbreviation", "")))
        return matches[0]["id"]

    return None


async def fetch_passage(reference: str, translation: str = "KJV") -> dict:
    """
    Returns {"text": str, "reference": str, "translation": str}
    reference: e.g. "John 3:16" or "Romans 8:28-30"
    """
    api_key = os.getenv("BIBLE_API_KEY", "")

    if api_key:
        bible_id = await find_bible_id(translation)
        if not bible_id:
            raise ValueError(f"Translation '{translation}' not found in api.bible")
        return await _fetch_from_api(reference, bible_id, api_key, translation)
    else:
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
    # Remove section headings and titles (para style s1, s2, d, ms, etc.)
    html = re.sub(r'<para[^>]+style="[sdm][^"]*"[^>]*>.*?</para>', "", html, flags=re.DOTALL)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_translation_label(abbreviation: str) -> str:
    """Return a clean display label, e.g. NIV11 -> NIV."""
    import re
    return re.sub(r"\d+$", "", abbreviation)
