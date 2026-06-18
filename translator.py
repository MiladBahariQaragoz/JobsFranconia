import html
import logging
import re

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

# Order in which the top fields are emitted.
_TOP_ORDER = ["🏢", "💶", "📍", "📂"]

RLM = chr(0x200F)  # U+200F RIGHT-TO-LEFT MARK — pins a line's base direction to RTL.

# Cyrillic block — used to decide whether a value still needs translating.
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def _strip_parens(s: str) -> str:
    """Remove (...) groups and collapse the leftover whitespace."""
    s = re.sub(r"\s*\([^)]*\)", "", s)
    return re.sub(r"\s{2,}", " ", s).strip()


def _has_cyrillic(s: str) -> bool:
    return bool(_CYRILLIC.search(s))


def _translate(text: str, target: str, source: str = "uk") -> str:
    """Translate a single chunk; format_='text' preserves newlines."""
    result = _get_client().translate(
        text, source_language=source, target_language=target, format_="text"
    )
    return html.unescape(result["translatedText"])


def _translate_batch(texts: list, target: str, source: str = "uk") -> list:
    """Translate a list of chunks in one API call; returns a list of strings."""
    if not texts:
        return []
    res = _get_client().translate(
        texts, source_language=source, target_language=target, format_="text"
    )
    if isinstance(res, dict):
        res = [res]
    return [html.unescape(r["translatedText"]) for r in res]


def _value_after_colon(line: str) -> str:
    """Return the part of a field line after the first colon (the value)."""
    parts = line.split(":", 1)
    return parts[1].strip() if len(parts) == 2 else ""


def translate_uk_to_fa(text: str) -> str:
    """Transform a Ukrainian/Russian job post into the channel format:

    German template + German values for the top (title, company, salary,
    location, category), Persian for the job description, application link kept.
    Returns the original text on failure so the event loop never crashes.
    """
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

        cat_word = category_raw.lstrip("#").strip() if category_raw else ""
        if cat_word and _has_cyrillic(cat_word):
            de_targets["__cat__"] = cat_word

        if de_targets:
            keys = list(de_targets.keys())
            results = _translate_batch([de_targets[k] for k in keys], "de")
            de = dict(zip(keys, results))
            if "__title__" in de:
                title = de["__title__"]
            for marker in ("🏢", "💶", "📍"):
                if marker in de:
                    top[marker] = de[marker]
            if "__cat__" in de:
                cat_word = de["__cat__"]

        category_tag = ""
        if cat_word:
            cat_clean = re.sub(r"\W+", "", cat_word)
            if cat_clean:
                category_tag = "#" + cat_clean

        # --- Description -> Persian ---
        desc_fa = ""
        desc_text = "\n".join(desc_lines).strip()
        if desc_text:
            desc_fa = _translate(desc_text, "fa")

        # --- Assemble the final message (plain text) ---
        out = []
        if title:
            out.append(title)
            out.append("")

        for marker in _TOP_ORDER:
            if marker == "📂":
                if category_tag:
                    out.append(f"📂 {_TOP_LABELS['📂']}: {category_tag}")
            elif top.get(marker):
                out.append(f"{marker} {_TOP_LABELS[marker]}: {top[marker]}")

        if desc_fa:
            out.append("")
            out.append("📝 Beschreibung (توضیحات):")
            # RTL-pin each Persian line so a Latin/German first word can't flip it.
            for dl in desc_fa.split("\n"):
                out.append((RLM + dl) if dl.strip() else dl)

        if contact_url:
            out.append("")
            out.append("👉 Kontakt:")
            out.append(contact_url)

        result_text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
        return result_text or text
    except Exception:
        logger.exception("Translation/transform failed — forwarding original text")
        return text
