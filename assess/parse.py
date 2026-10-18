"""Pull the claims out of an advert: registration, price, mileage, year, colour, fuel, engine.

Works on free text (a pasted advert or a Telegram message). Explicit values (CLI flags)
override anything found in the text.
"""
import re
from dataclasses import dataclass

# Current (AB12CDE), prefix (A123BCD) and suffix (ABC123D) plates. Dateless plates
# ("RS 200") are too ambiguous to find in free text; pass those explicitly.
PLATE_RE = re.compile(r"\b([A-Z]{2}\d{2}\s?[A-Z]{3}|[A-Z]\d{1,3}\s?[A-Z]{3}|[A-Z]{3}\s?\d{1,3}[A-Z])\b")
PRICE_RE = re.compile(r"£\s?(\d[\d,]*(?:\.\d{1,2})?)\s*(k)?\b", re.I)
MILES_RE = re.compile(r"\b(\d[\d,]*(?:\.\d)?)\s*(k)?\s*(?:miles|mi|mls)\b", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")
ENGINE_RE = re.compile(r"\b([1-6]\.\d)\s?(?:l|litre|ltr)?\b", re.I)
COLOURS = ["beige", "black", "blue", "bronze", "brown", "cream", "gold", "green", "grey", "gray",
           "maroon", "orange", "pink", "purple", "red", "silver", "turquoise", "white", "yellow"]
FUELS = ["petrol", "diesel", "hybrid", "electric"]


@dataclass
class Advert:
    registration: str | None = None
    price: int | None = None
    mileage: int | None = None
    year: int | None = None
    colour: str | None = None
    fuel: str | None = None
    engine_litres: float | None = None
    text: str = ""


def _number(value, thousands):
    number = float(value.replace(",", ""))
    return int(number * 1000) if thousands else int(number)


def parse_advert(text="", **explicit):
    """Advert from free text; keyword arguments (registration, price, ...) win over the text."""
    text = text or ""
    advert = Advert(text=text)

    plate = PLATE_RE.search(text.upper())
    if plate:
        advert.registration = plate.group(1).replace(" ", "")
    prices = [_number(m.group(1), m.group(2)) for m in PRICE_RE.finditer(text)]
    advert.price = next((p for p in prices if p >= 300), None)  # skip "£35 tax" and the like
    miles = MILES_RE.search(text)
    if miles:
        advert.mileage = _number(miles.group(1), miles.group(2))
    year = YEAR_RE.search(text)
    if year:
        advert.year = int(year.group(1))
    lower = text.lower()
    advert.colour = next((c for c in COLOURS if re.search(rf"\b{c}\b", lower)), None)
    advert.fuel = next((f for f in FUELS if re.search(rf"\b{f}\b", lower)), None)
    engine = ENGINE_RE.search(text)
    if engine:
        advert.engine_litres = float(engine.group(1))

    for key, value in explicit.items():
        if value not in (None, ""):
            setattr(advert, key, value)
    if advert.registration:
        advert.registration = re.sub(r"[^A-Z0-9]", "", advert.registration.upper())
    return advert
