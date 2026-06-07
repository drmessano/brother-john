#!/usr/bin/env python3
import os
import re
import time
import aiohttp

API_BASE = "https://rest.api.bible/v1"
HTTP_TIMEOUT = aiohttp.ClientTimeout(total=15)

_bible_cache: dict | None = None

# Verse text cache: (reference_lower, translation_upper) -> {result, expires_at}
_verse_cache: dict[tuple, dict] = {}
VERSE_CACHE_TTL = 6 * 3600  # 6 hours

# Ordered book lists for the picker: (usfm, display_name)
OT_BOOKS = [
    ("GEN","Genesis"), ("EXO","Exodus"), ("LEV","Leviticus"), ("NUM","Numbers"),
    ("DEU","Deuteronomy"), ("JOS","Joshua"), ("JDG","Judges"), ("RUT","Ruth"),
    ("1SA","1 Samuel"), ("2SA","2 Samuel"), ("1KI","1 Kings"), ("2KI","2 Kings"),
    ("1CH","1 Chronicles"), ("2CH","2 Chronicles"), ("EZR","Ezra"), ("NEH","Nehemiah"),
    ("EST","Esther"), ("JOB","Job"), ("PSA","Psalms"), ("PRO","Proverbs"),
    ("ECC","Ecclesiastes"), ("SNG","Song of Solomon"), ("ISA","Isaiah"),
    ("JER","Jeremiah"), ("LAM","Lamentations"), ("EZK","Ezekiel"), ("DAN","Daniel"),
    ("HOS","Hosea"), ("JOL","Joel"), ("AMO","Amos"), ("OBA","Obadiah"),
    ("JON","Jonah"), ("MIC","Micah"), ("NAH","Nahum"), ("HAB","Habakkuk"),
    ("ZEP","Zephaniah"), ("HAG","Haggai"), ("ZEC","Zechariah"), ("MAL","Malachi"),
]
NT_BOOKS = [
    ("MAT","Matthew"), ("MRK","Mark"), ("LUK","Luke"), ("JHN","John"),
    ("ACT","Acts"), ("ROM","Romans"), ("1CO","1 Corinthians"), ("2CO","2 Corinthians"),
    ("GAL","Galatians"), ("EPH","Ephesians"), ("PHP","Philippians"), ("COL","Colossians"),
    ("1TH","1 Thessalonians"), ("2TH","2 Thessalonians"), ("1TI","1 Timothy"),
    ("2TI","2 Timothy"), ("TIT","Titus"), ("PHM","Philemon"), ("HEB","Hebrews"),
    ("JAS","James"), ("1PE","1 Peter"), ("2PE","2 Peter"), ("1JN","1 John"),
    ("2JN","2 John"), ("3JN","3 John"), ("JUD","Jude"), ("REV","Revelation"),
]

# Reverse map: USFM -> display name
USFM_TO_NAME = {usfm: name for usfm, name in OT_BOOKS + NT_BOOKS}

# Chapter and verse caches
_chapters_cache: dict[str, list] = {}  # key: "{bible_id}:{usfm}"
_verses_cache:   dict[str, list] = {}  # key: "{bible_id}:{chapter_id}"


async def get_book_chapters(bible_id: str, usfm: str, api_key: str) -> list[dict]:
    """Return list of chapter dicts for a book. Cached."""
    key = f"{bible_id}:{usfm}"
    if key in _chapters_cache:
        return _chapters_cache[key]
    url = f"{API_BASE}/bibles/{bible_id}/books/{usfm}/chapters"
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.get(url, headers={"api-key": api_key}) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()
    # Filter out intro entries (id like "GEN.intro")
    chapters = [c for c in data.get("data", []) if not c["id"].endswith("intro")]
    _chapters_cache[key] = chapters
    return chapters


async def get_chapter_verses(bible_id: str, chapter_id: str, api_key: str) -> list[dict]:
    """Return list of verse dicts for a chapter. Cached."""
    key = f"{bible_id}:{chapter_id}"
    if key in _verses_cache:
        return _verses_cache[key]
    url = f"{API_BASE}/bibles/{bible_id}/chapters/{chapter_id}/verses"
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.get(url, headers={"api-key": api_key}) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()
    verses = data.get("data", [])
    _verses_cache[key] = verses
    return verses


# USFM book ID mapping
BOOK_IDS = {
    "genesis": "GEN", "exodus": "EXO", "leviticus": "LEV", "numbers": "NUM",
    "deuteronomy": "DEU", "joshua": "JOS", "judges": "JDG", "ruth": "RUT",
    "1 samuel": "1SA", "2 samuel": "2SA", "1 kings": "1KI", "2 kings": "2KI",
    "1 chronicles": "1CH", "2 chronicles": "2CH", "ezra": "EZR", "nehemiah": "NEH",
    "esther": "EST", "job": "JOB", "psalms": "PSA", "psalm": "PSA",
    "proverbs": "PRO", "ecclesiastes": "ECC", "song of solomon": "SNG",
    "song of songs": "SNG", "isaiah": "ISA", "jeremiah": "JER",
    "lamentations": "LAM", "ezekiel": "EZK", "daniel": "DAN", "hosea": "HOS",
    "joel": "JOL", "amos": "AMO", "obadiah": "OBA", "jonah": "JON",
    "micah": "MIC", "nahum": "NAH", "habakkuk": "HAB", "zephaniah": "ZEP",
    "haggai": "HAG", "zechariah": "ZEC", "malachi": "MAL",
    "matthew": "MAT", "mark": "MRK", "luke": "LUK", "john": "JHN",
    "acts": "ACT", "romans": "ROM", "1 corinthians": "1CO", "2 corinthians": "2CO",
    "galatians": "GAL", "ephesians": "EPH", "philippians": "PHP",
    "colossians": "COL", "1 thessalonians": "1TH", "2 thessalonians": "2TH",
    "1 timothy": "1TI", "2 timothy": "2TI", "titus": "TIT", "philemon": "PHM",
    "hebrews": "HEB", "james": "JAS", "1 peter": "1PE", "2 peter": "2PE",
    "1 john": "1JN", "2 john": "2JN", "3 john": "3JN", "jude": "JUD",
    "revelation": "REV",
}


def _reference_to_passage_id(reference: str) -> str | None:
    """Convert 'Philippians 2:1-11' -> 'PHP.2.1-PHP.2.11'"""
    ref = reference.strip()

    # Match: Book Chapter:Verse or Book Chapter:Verse-EndVerse
    m = re.match(r"^(.+?)\s+(\d+):(\d+)(?:-(\d+))?$", ref, re.IGNORECASE)
    if not m:
        # Try Book Chapter (whole chapter)
        m2 = re.match(r"^(.+?)\s+(\d+)$", ref, re.IGNORECASE)
        if m2:
            book_name = m2.group(1).lower()
            chapter = m2.group(2)
            usfm = BOOK_IDS.get(book_name)
            if usfm:
                return f"{usfm}.{chapter}"
        return None

    book_name = m.group(1).lower()
    chapter = m.group(2)
    verse_start = m.group(3)
    verse_end = m.group(4)

    usfm = BOOK_IDS.get(book_name)
    if not usfm:
        return None

    if verse_end:
        return f"{usfm}.{chapter}.{verse_start}-{usfm}.{chapter}.{verse_end}"
    else:
        return f"{usfm}.{chapter}.{verse_start}"


async def get_available_bibles() -> list[dict]:
    """Returns list of available bibles from api.bible. Results are cached."""
    global _bible_cache
    if _bible_cache is not None:
        return _bible_cache

    api_key = os.getenv("BIBLE_API_KEY", "")
    if not api_key:
        return []

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.get(f"{API_BASE}/bibles", headers={"api-key": api_key}) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()

    _bible_cache = data.get("data", [])
    return _bible_cache


def _normalize_abbr(abbr: str) -> str:
    """Strip language prefixes (eng, es, fr, etc.) and trailing digits."""
    abbr = re.sub(r"^[a-z]{2,3}(?=[A-Z])", "", abbr)
    abbr = re.sub(r"\d+$", "", abbr)
    return abbr.upper()


async def find_bible_id(abbreviation: str) -> str | None:
    """Look up a bible ID by abbreviation, normalizing both sides."""
    bibles = await get_available_bibles()
    abbr_upper = abbreviation.upper()
    normalized = _normalize_abbr(abbreviation)

    for b in bibles:
        if b.get("abbreviation", "").upper() == abbr_upper:
            return b["id"]

    matches = [b for b in bibles if _normalize_abbr(b.get("abbreviation", "")) == normalized]
    if matches:
        matches.sort(key=lambda b: len(b.get("abbreviation", "")))
        return matches[0]["id"]

    return None


async def fetch_passage(reference: str, translation: str = "KJV") -> dict:
    """
    Returns {"text": str, "reference": str, "translation": str}
    reference: e.g. "John 3:16" or "Romans 8:28-30"
    Caches results for VERSE_CACHE_TTL seconds.
    """
    key = (reference.strip().lower(), translation.upper())
    now = time.time()
    if key in _verse_cache and _verse_cache[key]["expires_at"] > now:
        return _verse_cache[key]["result"]

    api_key = os.getenv("BIBLE_API_KEY", "")
    if api_key:
        bible_id = await find_bible_id(translation)
        if not bible_id:
            raise ValueError(f"Translation '{translation}' not found in api.bible")
        result = await _fetch_from_api(reference, bible_id, api_key, translation)
    else:
        result = await _fetch_fallback(reference, translation)

    _verse_cache[key] = {"result": result, "expires_at": now + VERSE_CACHE_TTL}
    return result


async def _fetch_from_api(reference: str, bible_id: str, api_key: str, translation: str) -> dict:
    headers = {"api-key": api_key}

    # Try passages endpoint first (most reliable for references)
    passage_id = _reference_to_passage_id(reference)
    if passage_id:
        url = f"{API_BASE}/bibles/{bible_id}/passages/{passage_id}"
        params = {"content-type": "html", "include-notes": "false", "include-titles": "false"}
        async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    passage = data.get("data", {})
                    text = _strip_html(passage.get("content", ""))
                    ref = passage.get("reference", reference)
                    if text:
                        return {"text": text.strip(), "reference": ref, "translation": translation}

    # Fallback to search
    search_url = f"{API_BASE}/bibles/{bible_id}/search"
    params = {"query": reference, "limit": 1}
    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.get(search_url, headers=headers, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Bible API error {resp.status}: {await resp.text()}")
            data = await resp.json()

    passages = data.get("data", {}).get("passages", [])
    if passages:
        text = _strip_html(passages[0].get("content", ""))
        ref = passages[0].get("reference", reference)
        return {"text": text.strip(), "reference": ref, "translation": translation}

    verses = data.get("data", {}).get("verses", [])
    if verses:
        text = " ".join(_strip_html(v.get("text", "")) for v in verses)
        ref = f"{verses[0].get('reference', '')}–{verses[-1].get('reference', '')}" if len(verses) > 1 else verses[0].get("reference", reference)
        return {"text": text.strip(), "reference": ref, "translation": translation}

    raise ValueError(f"No results found for '{reference}'")


async def _fetch_fallback(reference: str, translation: str) -> dict:
    """labs.bible.org returns KJV JSON without an API key."""
    url = "https://labs.bible.org/api/"
    params = {"passage": reference, "type": "json"}

    async with aiohttp.ClientSession(timeout=HTTP_TIMEOUT) as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                raise ValueError(f"Fallback Bible API error {resp.status}")
            data = await resp.json(content_type=None)

    if not data or not isinstance(data, list):
        raise ValueError(f"No results for '{reference}'")

    verses = [v.get("text", "").strip() for v in data]
    first, last = data[0], data[-1]
    book = first.get("bookname", "")
    ch = first.get("chapter", "")
    v_start = first.get("verse", "")
    v_end = last.get("verse", "")
    ref = f"{book} {ch}:{v_start}" if v_start == v_end else f"{book} {ch}:{v_start}-{v_end}"

    return {"text": " ".join(verses), "reference": ref, "translation": "KJV (fallback)"}


def _strip_html(html: str) -> str:
    # Remove section headings: <p class="s1">, <p class="s2">, <p class="d">, etc.
    html = re.sub(r'<p[^>]+class="[sdm][^"]*"[^>]*>.*?</p>', "", html, flags=re.DOTALL)
    # Remove verse number spans
    html = re.sub(r'<span[^>]+data-number="[^"]*"[^>]*>\d+</span>', "", html)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", html)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_translation_label(abbreviation: str) -> str:
    """Return a clean display label, e.g. NIV11 -> NIV."""
    return re.sub(r"\d+$", "", abbreviation)
