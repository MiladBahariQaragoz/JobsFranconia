import logging

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        from google.cloud import translate_v2 as translate
        _client = translate.Client()
    return _client


def translate_uk_to_fa(text: str) -> str:
    """Translate Ukrainian text to Persian. Returns original text on failure."""
    if not text or not text.strip():
        return text
    try:
        import re
        
        processed_text = text
        
        # Pre-process: Extract the category hashtag (on the 📂 line) to keep and translate
        # We look for a hashtag on the same line as the 📂 emoji
        category_match = re.search(r'(📂[^\n#]*)#(\S+)', processed_text)
        category_hashtag = None
        if category_match:
            category_hashtag = category_match.group(2)
            processed_text = processed_text.replace(f"#{category_hashtag}", "ZZZCATTAGZZZ")
            
        # Pre-process: Remove all other hashtags completely
        processed_text = re.sub(r'#\S+', '', processed_text)
        
        # Pre-process: Remove the "🛡 Status:" line completely
        processed_text = re.sub(r'🛡.*?(?:\n|$)', '', processed_text)
        
        # Pre-process: Extract Company and Location to prevent them from being translated
        # Company usually comes after 🏢 and Location after 📍
        # We match everything after the colon on those lines
        company_match = re.search(r'(🏢.*?:)\s*([^\n]+)', processed_text)
        company_name = None
        if company_match:
            company_name = company_match.group(2).strip()
            processed_text = processed_text.replace(company_name, "ZZZCOMPNAMEZZZ")
            
        location_match = re.search(r'(📍.*?:)\s*([^\n]+)', processed_text)
        location_name = None
        if location_match:
            location_name = location_match.group(2).strip()
            processed_text = processed_text.replace(location_name, "ZZZLOCNAMEZZZ")
        
        # Translate the main text using format_="text" to preserve newlines
        result = _get_client().translate(processed_text, source_language="uk", target_language="fa", format_="text")
        translated = result["translatedText"]
        
        # Post-process: Restore Company and Location names
        # Google Translate sometimes lowercases English placeholders or adds spaces
        if company_name:
            translated = re.sub(r'z\s*z\s*z\s*c\s*o\s*m\s*p\s*n\s*a\s*m\s*e\s*z\s*z\s*z', company_name, translated, flags=re.IGNORECASE)
        if location_name:
            translated = re.sub(r'z\s*z\s*z\s*l\s*o\s*c\s*n\s*a\s*m\s*e\s*z\s*z\s*z', location_name, translated, flags=re.IGNORECASE)
            
        # Post-process: Translate and restore the Category hashtag
        if category_hashtag:
            tag_result = _get_client().translate(category_hashtag, source_language="uk", target_language="fa", format_="text")
            translated_tag = tag_result["translatedText"]
            # Format as valid Persian Telegram hashtag
            translated_tag = translated_tag.replace(" ", "_").replace("‌", "_")
            translated_tag = re.sub(r'[^\w_]', '', translated_tag)
            # Reinsert translated hashtag
            translated = re.sub(r'z\s*z\s*z\s*c\s*a\s*t\s*t\s*a\s*g\s*z\s*z\s*z', f"#{translated_tag}", translated, flags=re.IGNORECASE)
        
        # Post-process: Convert Markdown bold to HTML bold since telegram parse_mode is HTML
        translated = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', translated)
        
        # Post-process: Convert Markdown links to HTML links
        translated = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2">\1</a>', translated)
        
        # Clean up any potential double spacing left from removing hashtags
        translated = re.sub(r' {2,}', ' ', translated).strip()
        
        return translated
    except Exception:
        logger.exception("Translation failed — forwarding original text")
        return text
