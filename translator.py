import html
import json
import logging
import os
import re
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import translate_v2 as translate
        _client = translate.Client()
    return _client


# Fixed German labels for the structured "top" of a post, keyed by the leading
# emoji marker. These are a template — translated once here, never via the API —
# so the top never carries leftover Ukrainian/Russian text.
_TOP_LABELS = {
    "🏢": "Firma",
    "💶": "Gehalt",
    "📍": "Standort",
    "📂": "Kategorie",
}

# Order in which the top fields are emitted. (📂 Kategorie is intentionally
# dropped — see translate_uk_to_fa — because the source values are unreliable.)
_TOP_ORDER = ["🏢", "💶", "📍"]

RLM = chr(0x200F)  # U+200F RIGHT-TO-LEFT MARK — pins a line's base direction to RTL.

# Per-target-language output settings. The top fields are always translated to
# German; only the description language and the localized labels differ.
#   code       — Google Translate target code for the description
#   desc_label — localized word shown beside the German "Beschreibung" header
#   link_label — localized clickable label for the application link
#   rtl        — True for right-to-left scripts (pin each line with RLM)
_LANGS = {
    "fa": {  # Persian
        "code": "fa",
        "desc_label": "توضیحات",
        "link_label": "لینک آگهی",
        "rtl": True,
    },
}

# Cyrillic block — used to decide whether a value still needs translating.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _strip_parens(s: str) -> str:
    """Remove (...) groups and collapse the leftover whitespace."""
    s = re.sub(r"\s*\([^)]*\)", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _has_cyrillic(s: str) -> bool:
    return bool(_CYRILLIC.search(s))


# --- MyMemory backup translator -------------------------------------------
# Free fallback used only when Google Translate raises (quota, 403, outage). Uses
# stdlib HTTP (urllib), matching the repo convention. MyMemory caps each request
# near 500 characters, so multi-line text is translated line by line and any long
# line is split on spaces. Set MYMEMORY_EMAIL to lift the free daily quota
# (~50k vs ~5k characters/day); it is read straight from the environment.
_MYMEMORY_URL = "https://api.mymemory.translated.net/get"
_MYMEMORY_LIMIT = 480  # per-request character budget (below MyMemory's ~500 cap)


def _mymemory_get(text: str, source: str, target: str) -> str:
    """Translate one <=_MYMEMORY_LIMIT-char chunk via MyMemory. Raises on error."""
    params = {"q": text, "langpair": f"{source}|{target}"}
    email = os.environ.get("MYMEMORY_EMAIL", "").strip()
    if email:
        params["de"] = email
    url = f"{_MYMEMORY_URL}?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    translated = (data.get("responseData") or {}).get("translatedText")
    if not translated or str(data.get("responseStatus")) != "200":
        raise RuntimeError(f"MyMemory error (status={data.get('responseStatus')}): {str(data)[:200]}")
    return html.unescape(translated)


def _mymemory_translate(text: str, source: str, target: str) -> str:
    """Translate multi-line text via MyMemory, preserving line breaks and staying
    under the per-request cap. Best-effort: returns the original text if the
    backup itself fails, so the pipeline never crashes."""
    if not text or not text.strip():
        return text
    try:
        out = []
        for line in text.split("\n"):
            if not line.strip():
                out.append("")
                continue
            if len(line) <= _MYMEMORY_LIMIT:
                out.append(_mymemory_get(line, source, target))
                continue
            # Long line: translate in <=_MYMEMORY_LIMIT-char, space-aligned pieces.
            pieces, cur = [], ""
            for word in line.split(" "):
                if cur and len(cur) + 1 + len(word) > _MYMEMORY_LIMIT:
                    pieces.append(cur)
                    cur = word
                else:
                    cur = f"{cur} {word}".strip()
            if cur:
                pieces.append(cur)
            out.append(" ".join(_mymemory_get(p, source, target) for p in pieces))
        return "\n".join(out)
    except Exception:
        logger.exception("MyMemory backup translation failed (%s->%s)", source, target)
        return text


def _translate(text: str, target: str, source: str = "uk") -> str:
    """Translate a single chunk; format_='text' preserves newlines. Falls back to
    the MyMemory backup if Google Translate fails."""
    try:
        result = _get_client().translate(
            text, source_language=source, target_language=target, format_="text"
        )
        return html.unescape(result["translatedText"])
    except Exception:
        logger.warning("Google Translate failed (%s->%s); using MyMemory backup", source, target)
        return _mymemory_translate(text, source, target)


def _translate_batch(texts: list, target: str, source: str = "uk") -> list:
    """Translate a list of chunks in one API call; returns a list of strings.
    Falls back to translating each chunk via the MyMemory backup on failure."""
    if not texts:
        return []
    try:
        res = _get_client().translate(
            texts, source_language=source, target_language=target, format_="text"
        )
        if isinstance(res, dict):
            res = [res]
        return [html.unescape(r["translatedText"]) for r in res]
    except Exception:
        logger.warning("Google batch translate failed (%s->%s); using MyMemory backup", source, target)
        return [_mymemory_translate(t, source, target) for t in texts]


def _value_after_colon(line: str) -> str:
    """Return the part of a field line after the first colon (the value)."""
    parts = line.split(":", 1)
    return parts[1].strip() if len(parts) == 2 else ""


def _ensure_scheme(url: str) -> str:
    """Prepend https:// to scheme-less links so Telegram renders a real <a>.
    Auto-linked posts often carry a bare domain like 'www.joboo.de/x'."""
    if url and not re.match(r"[a-z][a-z0-9+.\-]*://", url, re.I):
        return "https://" + url
    return url


def translate_uk(text: str, lang: str = "fa", link_url: str | None = None) -> str:
    """Transform a Ukrainian/Russian job post into the channel format:

    German template + German values for the top (title, company, salary,
    location, category), `lang` for the job description, application link kept.
    `lang` is one of `_LANGS` (currently only "fa" Persian).

    `link_url` is the authoritative application URL extracted from the message's
    Telegram entities (see main.py). The source channel sometimes hides the link
    behind a clickable label or auto-links a scheme-less domain, in which cases
    the visible text carries no 'https://...' to scrape — `link_url` is then the
    only way to recover it. Used as a fallback when the visible 👉 line has no
    parseable URL. Returns the original text on failure so the loop never crashes.
    """
    cfg = _LANGS.get(lang, _LANGS["fa"])
    if not text or not text.strip():
        return text
    try:
        # Strip Markdown bold/underline markers up front — the post goes out as
        # plain text, so leftover ** / __ would show literally.
        text = text.replace("**", "").replace("__", "")

        title = None
        top = {}            # emoji marker -> raw value
        category_raw = None
        desc_lines = []
        contact_url = None

        in_description = False

        for raw_line in text.split("\n"):
            stripped = raw_line.strip()

            if not stripped:
                if in_description and desc_lines:
                    desc_lines.append("")  # keep paragraph breaks inside description
                continue

            marker = next(
                (m for m in ("🏢", "💶", "📍", "📂", "🛡", "📝") if stripped.startswith(m)),
                None,
            )

            if marker == "🛡":                      # status line -> delete
                in_description = False
                continue
            if marker == "📝":                      # description header
                in_description = True
                tail = _value_after_colon(stripped)
                if tail:
                    desc_lines.append(tail)
                continue
            if marker == "📂":                      # category -> German hashtag
                in_description = False
                category_raw = _value_after_colon(stripped)
                continue
            if marker in ("🏢", "💶", "📍"):          # top values
                in_description = False
                top[marker] = _value_after_colon(stripped)
                continue

            if stripped.startswith("👉"):            # contact line -> keep the URL
                in_description = False
                m = re.search(r"https?://\S+", stripped)
                if m:
                    contact_url = m.group(0).rstrip(").,]")
                continue

            if re.fullmatch(r"(#\S+\s*)+", stripped):  # trailing hashtag block -> drop
                continue

            if in_description:
                desc_lines.append(stripped)
            elif title is None:
                title = stripped

        # Fall back to the authoritative entity URL when the visible 👉 line had
        # no scrapable 'https://...' (clickable label / scheme-less auto-link).
        if not contact_url and link_url:
            contact_url = link_url

        # --- Collect everything that needs German translation into one batch ---
        # Rule: translate to German only what still contains Cyrillic; Latin/German
        # values (e.g. "DEMIR GmbH", "90449 Nürnberg") are kept verbatim.
        de_targets = {}  # key -> source text

        if title:
            title = _strip_parens(title)
            if _has_cyrillic(title):
                de_targets["__title__"] = title

        for marker in ("🏢", "💶", "📍"):
            top[marker] = _strip_parens(top.get(marker, ""))
            if top[marker] and _has_cyrillic(top[marker]):
                de_targets[marker] = top[marker]

        # Category (📂) is intentionally dropped from the output — the source
        # values are often inaccurate — so it is neither translated nor emitted.
        # The 📂 line is still parsed above only so it can't leak into the
        # description.

        if de_targets:
            keys = list(de_targets.keys())
            results = _translate_batch([de_targets[k] for k in keys], "de")
            de = dict(zip(keys, results))
            if "__title__" in de:
                title = de["__title__"]
            for marker in ("🏢", "💶", "📍"):
                if marker in de:
                    top[marker] = de[marker]

        # --- Description -> target language ---
        desc_translated = ""
        desc_text = "\n".join(desc_lines).strip()
        if desc_text:
            desc_translated = _translate(desc_text, cfg["code"])

        # --- Assemble the final message (HTML) ---
        # The post is sent with parse_mode=HTML, so every dynamic value is escaped
        # and only the application link is emitted as an <a> tag — this lets the
        # link show a clickable Persian label instead of a bare URL.
        def esc(s: str) -> str:
            return html.escape(s, quote=False)

        out = []
        if title:
            out.append(esc(title))
            out.append("")

        for marker in _TOP_ORDER:
            if top.get(marker):
                out.append(f"{marker} {_TOP_LABELS[marker]}: {esc(top[marker])}")

        if desc_translated:
            out.append("")
            out.append(f"📝 Beschreibung ({cfg['desc_label']}):")
            # For RTL targets, pin each line so a Latin/German first word can't
            # flip it; LTR targets (e.g. Azerbaijani) need no marker.
            for dl in desc_translated.split("\n"):
                if cfg["rtl"]:
                    out.append((RLM + esc(dl)) if dl.strip() else dl)
                else:
                    out.append(esc(dl))

        if contact_url:
            out.append("")
            # Clickable localized label linking to the original application URL.
            href = html.escape(_ensure_scheme(contact_url), quote=True)
            prefix = RLM if cfg["rtl"] else ""
            out.append(f'{prefix}👉 <a href="{href}">{esc(cfg["link_label"])}</a>')

        result_text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
        return result_text or text
    except Exception:
        logger.exception("Translation/transform failed — forwarding original text")
        # Escape so the fallback is still valid under parse_mode=HTML.
        return html.escape(text)


def translate_uk_to_fa(text: str) -> str:
    """Backwards-compatible alias for the Persian translation."""
    return translate_uk(text, "fa")
