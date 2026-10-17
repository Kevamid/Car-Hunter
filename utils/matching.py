"""Title matching shared by every scraper."""
import re


def alias_in_text(text, alias):
    """Whole-word match, so 'rs' hits "Clio RS" but not "owners", and '200' hits
    "Clio 200" but not "12000 miles". Spaces/hyphens in the alias are optional."""
    words = [re.escape(w) for w in re.split(r"[\s\-]+", str(alias).lower()) if w]
    if not words:
        return False
    return re.search(r"(?<![a-z0-9])" + r"[\s\-]*".join(words) + r"(?![a-z0-9])", str(text).lower()) is not None


def any_alias_in_text(text, aliases):
    return any(alias_in_text(text, a) for a in aliases)
