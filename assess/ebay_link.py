"""Assess an eBay listing from its link, using eBay's official Browse API (no page fetching).

eBay masks the "Reg. Mark" item specific (e.g. "**13 ***"), but dealers usually put the plate in
the title or description. A plate found there is only used if it fits the mask. Private sellers
rarely include it, so without a plate the reply covers what the listing itself shows and asks
for the registration.
"""
import html
import re
from datetime import date, datetime

from assess.checks import check_advert_text, check_model_notes, run_checks
from assess.parse import PLATE_RE, Advert
from assess.repairs import add_repairs
from assess.report import build_report, verdict
from assess.vehicle import Vehicle, vehicle_from_api

ITEM_RE = re.compile(r"ebay\.[a-z.]+/itm/(?:[^/\s?#]+/)?(\d{9,15})", re.I)


def find_item_id(text):
    """Legacy eBay item number from a link, or None."""
    match = ITEM_RE.search(text or "")
    return match.group(1) if match else None


def _aspects(detail):
    return {a.get("name"): a.get("value") for a in detail.get("localizedAspects") or []}


def _int(text):
    digits = re.sub(r"[^\d]", "", str(text or ""))
    return int(digits) if digits else None


def _fits_mask(plate, mask):
    """'AB13CDE' fits '**13 ***': every visible character of the mask is in the same place."""
    mask = re.sub(r"\s", "", mask or "").upper()
    if not mask or "*" not in mask:
        return True
    if len(mask) != len(plate):
        return all(chunk in plate for chunk in re.split(r"\*+", mask) if chunk)
    return all(m == "*" or m == p for m, p in zip(mask, plate))


def _visible_text(description):
    """Description as the buyer reads it: dealer templates carry tens of KB of CSS/JS whose
    tokens look like plates, so drop style/script blocks and tags first."""
    text = re.sub(r"<(style|script)\b.*?</\1>", " ", description or "", flags=re.S | re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))


REG_CONTEXT_RE = re.compile(r"\b(?:reg(?:istration)?|vrm|number plate)\b[^A-Za-z0-9]{0,12}$", re.I)


def registration_from_listing(detail):
    """A plate the listing shows, or None. With eBay's masked Reg. Mark ("**13 ***"), only a plate
    that fits it counts. Without a mask, only a single plate written right after "reg" /
    "registration" counts, because random tokens can look like plates."""
    text = f"{detail.get('title', '')} {_visible_text(detail.get('description'))}"
    mask = _aspects(detail).get("Reg. Mark") or _aspects(detail).get("Registration Number")
    found = []
    for match in PLATE_RE.finditer(text.upper()):
        plate = match.group(1).replace(" ", "")
        if mask and "*" in mask:
            ok = _fits_mask(plate, mask)
        else:
            ok = bool(REG_CONTEXT_RE.search(text[max(0, match.start() - 30):match.start()]))
        if ok and plate not in found:
            found.append(plate)
    return found[0] if len(found) == 1 else None


def advert_from_listing(detail, registration=None):
    aspects = _aspects(detail)
    engine = str(aspects.get("Engine Size") or "")
    litres = None
    if re.fullmatch(r"\d{3,4}", engine):
        litres = round(int(engine) / 1000, 1)
    elif re.fullmatch(r"\d\.\d", engine):
        litres = float(engine)
    price = (detail.get("price") or {}).get("value")
    text = _visible_text(detail.get("description"))
    return Advert(registration=registration, price=round(float(price)) if price else None,
                  mileage=_int(aspects.get("Mileage")), year=_int(aspects.get("Year")),
                  colour=aspects.get("Exterior Colour") or aspects.get("Colour"),
                  fuel=aspects.get("Fuel Type") or aspects.get("Fuel"), engine_litres=litres,
                  text=f"{detail.get('title', '')}\n{text}")


def listing_header(detail):
    aspects = _aspects(detail)
    seller = {"BUSINESS": "Dealer", "INDIVIDUAL": "Private seller"}.get((detail.get("seller") or {}).get("sellerAccountType"))
    price = (detail.get("price") or {}).get("value")
    facts = [seller,
             f"{aspects['Number of Previous Owners']} previous owners" if aspects.get("Number of Previous Owners") else None,
             f"MOT to {aspects['MOT Expiry']}" if aspects.get("MOT Expiry") else None,
             f"service history: {aspects['Service History Available'].lower()}" if aspects.get("Service History Available") else None,
             f"logbook (V5C): {aspects['V5C Registration Document'].lower()}" if aspects.get("V5C Registration Document") else None]
    lines = [f"eBay listing: {detail.get('title', '').strip()}" + (f" · £{round(float(price)):,}" if price else "")]
    if any(facts):
        lines.append(" · ".join(f for f in facts if f))
    return lines


def _listing_vehicle(detail):
    """A record-less Vehicle from eBay's own specifics, so model weak-spot notes can apply."""
    aspects = _aspects(detail)
    first = aspects.get("Date of 1st Registration") or ""
    try:
        registered = datetime.strptime(first, "%Y%m%d").date()
    except ValueError:
        registered = date(_int(aspects.get("Year")), 1, 1) if _int(aspects.get("Year")) else None
    engine = aspects.get("Engine Size") or ""
    return Vehicle(registration="", make=aspects.get("Manufacturer") or "",
                   model=f"{aspects.get('Model') or ''} {detail.get('title', '')}",  # title helps spot e.g. "RS"
                   colour=None, fuel=None, engine_cc=_int(engine) if re.fullmatch(r"\d{3,4}", engine) else None,
                   first_registered=registered, recall="Unknown", first_mot_due=None)


def assess_ebay(item_id, client, lookup, registration=None, today=None):
    """Reply text for an eBay link. `registration` (typed by the user) wins over the listing's own."""
    from utils.ebay_api import EbayApiError
    try:
        detail = client.get("/buy/browse/v1/item/get_item_by_legacy_id", legacy_item_id=item_id)
    except EbayApiError as exc:
        if exc.status == 404:
            return "I couldn't find that eBay listing. It may have ended or been removed."
        return f"eBay returned an error ({exc.status}). Try again later."

    header = listing_header(detail)
    found = registration or registration_from_listing(detail)
    if found:
        payload = lookup(found)
        if payload is not None:
            advert = advert_from_listing(detail, registration=found)
            vehicle = vehicle_from_api(payload)
            source = "you sent" if registration else "found in the listing"
            header.append(f"Registration ({source}): {found}")
            report = build_report(vehicle, advert, run_checks(vehicle, advert, today=today))
            return "\n".join(header) + "\n\n" + add_repairs(report, vehicle, client)
        header.append(f"Registration {found} has no MOT record; check it with the seller.")

    advert = advert_from_listing(detail)
    vehicle = _listing_vehicle(detail)
    today = today or date.today()
    findings = check_advert_text(vehicle, advert, today) + check_model_notes(vehicle, advert, today)
    lines = header + ["", "The listing doesn't show the registration, so I can't check the MOT history yet. "
                          "Ask the seller for it, then send it here with the link."]
    if findings:
        icons = {"red": "🔴", "amber": "🟠", "green": "🟢", "info": "ℹ️"}
        lines += ["", f"From the listing alone: {verdict(findings)}"] + [f"{icons[f.severity]} {f.message}" for f in findings]
        questions = list(dict.fromkeys(f.question for f in findings if f.question))
        if questions:
            lines += ["", "Ask the seller:"] + [f"• {q}" for q in questions]
    return "\n".join(lines)
