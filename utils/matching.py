"""Title matching shared by every scraper."""
import re
import unicodedata


def _fold(text):
    """Lower case without accents: "Mégane" -> "megane", so sellers' spellings still match."""
    return "".join(c for c in unicodedata.normalize("NFKD", str(text).lower()) if not unicodedata.combining(c))


def alias_in_text(text, alias):
    """Whole-word match, so 'rs' hits "Clio RS" but not "owners", and '200' hits
    "Clio 200" but not "12000 miles". Spaces/hyphens in the alias are optional."""
    words = [re.escape(w) for w in re.split(r"[\s\-]+", _fold(alias)) if w]
    if not words:
        return False
    # a number glued to ",000" / ".5" or after "1," is part of a bigger number ("172,000 miles", "£1,200")
    return re.search(r"(?<![a-z0-9])(?<!\d[.,])" + r"[\s\-]*".join(words) + r"(?![a-z0-9])(?![.,]\d)",
                     _fold(text)) is not None


def any_alias_in_text(text, aliases):
    return any(alias_in_text(text, a) for a in aliases)


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", _fold(text))


def title_matches(title, words, unmatched, model_applied, aliases):
    """Title check for whatever the site did not filter itself.

    - aliases given: the title needs one alias as a whole word ("Clio 200", "Clio RS"), plus
      the base model word if the site applied no model filter ("Megane RS" fails "Clio Sport").
    - no aliases: every model word the site did not apply must be in the title, ignoring
      spaces and punctuation ("MX-5" matches "Mazda MX5", "GT86" matches "GT 86").
    """
    if aliases:
        base_ok = model_applied or not words or _norm(words[0]) in _norm(title)
        return base_ok and any_alias_in_text(title, aliases)
    return all(_norm(w) in _norm(title) for w in unmatched)
