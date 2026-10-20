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


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


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
