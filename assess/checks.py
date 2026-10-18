"""Rules that compare an advert with the official record.

Each check returns Findings: a severity, a plain-English message and, where useful, a
question to put to the seller. Checks never raise on missing data; they just skip.
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta

RED, AMBER, GREEN, INFO = "red", "amber", "green", "info"
SEVERITY_ORDER = {RED: 0, AMBER: 1, GREEN: 2, INFO: 3}

RETEST_WINDOW = timedelta(days=30)   # a fail followed by a pass within this is one MOT "event"
GAP_LIMIT = timedelta(days=456)      # ~15 months between tests counts as a gap
MILEAGE_TOLERANCE = 250              # miles; allows for rounding and a short test drive

# Advisory themes, matched against defect text. Order matters only for display.
THEMES = {
    "corrosion": r"corro|rust",
    "oil leak": r"oil leak|leaking oil|leak(?:s|ing)? of oil",
    "brakes": r"\bbrake|\bdisc|\bpad\b|\bpads\b|caliper",
    "suspension": r"suspension|shock absorber|\bbush|ball joint|coil spring|anti-roll|wishbone",
    "steering": r"steering|track rod",
    "exhaust": r"exhaust",
    "tyres": r"\btyre",
}
THEME_QUESTIONS = {
    "corrosion": "Can you send close-up photos of the sills, arches and underside? Any rust repairs done?",
    "oil leak": "Has the oil leak been diagnosed and fixed? Any invoices?",
    "brakes": "When were the brakes last replaced?",
    "suspension": "Has any suspension work been done recently?",
}
ADVERT_FLAGS = [
    (RED, r"\bcat(?:egory)?\s?[abcdsn]\b", "Mentions a write-off category (Cat S/N/C/D)"),
    (RED, r"salvage|spares or repair|non[- ]runner", "Described as salvage, spares/repair or non-runner"),
    (RED, r"no (?:v5c?|logbook)", "No logbook (V5C) mentioned"),
    (AMBER, r"\bexport\b", "Mentions export"),
    (AMBER, r"px to clear|sold as seen|trade sale|no warranty", "Trade-clearance wording (\"PX to clear\", \"sold as seen\")"),
    (AMBER, r"engine (?:management )?light|\beml\b|warning light", "Mentions a warning light"),
    (AMBER, r"needs (?:some )?work|project|needs tlc", "Described as needing work"),
    (AMBER, r"no mot\b|mot expired", "Says it has no MOT"),
]
COLOUR_ALIASES = {"gray": "grey"}


@dataclass
class Finding:
    severity: str
    message: str
    question: str | None = None


def _fmt_date(d):
    return d.strftime("%b %Y")


def _years(delta):
    return delta.days / 365.25


def check_too_new(vehicle, advert, today):
    if vehicle.tests:
        return []
    due = f" (first MOT due {_fmt_date(vehicle.first_mot_due)})" if vehicle.first_mot_due else ""
    return [Finding(INFO, f"No MOT history yet{due}: mileage and condition can't be checked against records.")]


def check_recall(vehicle, advert, today):
    if vehicle.recall == "Yes":
        return [Finding(AMBER, "Has an outstanding safety recall.",
                        "Has the outstanding recall been done? (Recall work is free at a franchised dealer.)")]
    if vehicle.recall == "No":
        return [Finding(GREEN, "No outstanding safety recalls.")]
    return []


def check_identity(vehicle, advert, today):
    out = []
    if advert.year and vehicle.year:
        diff = abs(advert.year - vehicle.year)
        if diff >= 2:
            out.append(Finding(RED, f"Advert says {advert.year}, but first registered "
                                    f"{_fmt_date(vehicle.first_registered)}.",
                               "Why does the advertised year differ from the registration date?"))
        elif diff == 1:
            out.append(Finding(INFO, f"Advert says {advert.year}; first registered "
                                     f"{_fmt_date(vehicle.first_registered)} (often just model year vs registration)."))
    if advert.colour and vehicle.colour:
        said = COLOUR_ALIASES.get(advert.colour.lower(), advert.colour.lower())
        record = COLOUR_ALIASES.get(vehicle.colour.lower(), vehicle.colour.lower())
        if said not in record and record not in said:
            out.append(Finding(AMBER, f"Advert says {advert.colour.lower()}, the record says {vehicle.colour.lower()}.",
                               "Has the car been resprayed or wrapped? Is the colour change on the V5C?"))
    if advert.fuel and vehicle.fuel and advert.fuel.lower() not in vehicle.fuel.lower():
        out.append(Finding(RED, f"Advert says {advert.fuel}, the record says {vehicle.fuel.lower()}.",
                           "Can you confirm the fuel type? The official record differs."))
    if advert.engine_litres and vehicle.engine_cc:
        record_litres = round(vehicle.engine_cc / 1000, 1)
        if abs(record_litres - advert.engine_litres) > 0.15:
            out.append(Finding(AMBER, f"Advert says {advert.engine_litres}L, the record says {vehicle.engine_cc}cc.",
                               "Has the engine been changed?"))
    return out


def check_mot_status(vehicle, advert, today):
    if not vehicle.tests:
        return []
    latest = vehicle.tests[-1]
    if not latest.passed:
        return [Finding(RED, f"Latest MOT ({latest.date:%d %b %Y}) was a fail, with no pass since.",
                        "The last MOT failed. What was fixed, and will it have a fresh MOT?")]
    expiry = max((t.expiry for t in vehicle.tests if t.passed and t.expiry), default=None)
    if not expiry:
        return []
    if expiry < today:
        return [Finding(RED, f"MOT expired on {expiry:%d %b %Y}.", "Will it be sold with a new MOT?")]
    if expiry - today <= timedelta(days=60):
        return [Finding(AMBER, f"MOT runs out soon ({expiry:%d %b %Y}).", "Will you put a fresh MOT on it before sale?")]
    return [Finding(GREEN, f"MOT valid until {expiry:%d %b %Y}.")]


def check_mileage(vehicle, advert, today):
    out, readings = [], vehicle.readings
    for (d1, m1), (d2, m2) in zip(readings, readings[1:]):
        if m2 < m1 - 50:
            out.append(Finding(RED, f"Recorded mileage went DOWN: {m1:,} ({d1:%b %Y}) then {m2:,} ({d2:%b %Y}).",
                               "Why did the mileage go down between MOTs? (Clocking, a replaced clock or a tester typo?)"))
    unread = [t for t in vehicle.tests if t.odometer == "UNREADABLE"]
    if unread:
        out.append(Finding(AMBER, f"Odometer recorded as unreadable at {len(unread)} MOT(s).",
                           "Has the instrument cluster ever been replaced?"))
    if not readings:
        return out

    last_date, last_miles = readings[-1]
    if advert.mileage is not None:
        if advert.mileage < last_miles - MILEAGE_TOLERANCE:
            out.append(Finding(RED, f"Advert says {advert.mileage:,} miles, but the {last_date:%b %Y} MOT recorded {last_miles:,}.",
                               "Why is the advertised mileage lower than the last MOT reading?"))
        elif not any(f.severity == RED for f in out):
            out.append(Finding(GREEN, f"Advert mileage ({advert.mileage:,}) is consistent with the MOT history "
                                      f"(last recorded {last_miles:,} in {last_date:%b %Y})."))

    if vehicle.first_registered and last_date > vehicle.first_registered:
        per_year = last_miles / _years(last_date - vehicle.first_registered)
        if per_year > 20000:
            out.append(Finding(INFO, f"High annual use: about {per_year:,.0f} miles a year."))
        elif per_year < 2000:
            out.append(Finding(INFO, f"Very low use: about {per_year:,.0f} miles a year.",
                               "How has it been stored and kept running with such low mileage?"))
        else:
            out.append(Finding(INFO, f"Average use about {per_year:,.0f} miles a year."))
    return out


def check_gaps(vehicle, advert, today):
    out = []
    for a, b in zip(vehicle.tests, vehicle.tests[1:]):
        if b.date - a.date > GAP_LIMIT:
            months = round((b.date - a.date).days / 30.4)
            out.append(Finding(AMBER, f"No MOT for {months} months ({a.date:%b %Y} to {b.date:%b %Y}).",
                               f"Why was there no MOT between {a.date:%b %Y} and {b.date:%b %Y}? (SORN, stored, or a repair?)"))
    return out


def _events(tests):
    """Group a fail and its quick retest(s) into one MOT event."""
    events = []
    for test in tests:
        if events and test.date - events[-1][-1].date <= RETEST_WINDOW:
            events[-1].append(test)
        else:
            events.append([test])
    return events


def check_failures(vehicle, advert, today):
    if not vehicle.tests:
        return []
    out, events = [], _events(vehicle.tests)
    failed = [e for e in events if not e[0].passed]
    if len(events) >= 4 and len(failed) / len(events) >= 0.5:
        out.append(Finding(AMBER, f"Failed {len(failed)} of {len(events)} MOTs first time."))
    elif len(events) >= 3 and not failed:
        out.append(Finding(GREEN, f"Passed all {len(events)} MOTs first time."))
    elif failed:
        out.append(Finding(INFO, f"Failed {len(failed)} of {len(events)} MOTs first time."))

    dangerous = [(t, d) for t in vehicle.tests for d in t.defects if d.dangerous or d.type == "DANGEROUS"]
    if dangerous:
        t, d = dangerous[-1]
        recent = today - t.date <= timedelta(days=730)
        out.append(Finding(RED if recent else AMBER,
                           f"Dangerous defect recorded {t.date:%b %Y}: {d.text}.",
                           "What was done to fix the dangerous defect, and by whom?"))
    return out


def check_recurring(vehicle, advert, today):
    out = []
    for theme, pattern in THEMES.items():
        years = sorted({t.date.year for t in vehicle.tests for d in t.defects if re.search(pattern, d.text, re.I)})
        if len(years) >= 3:
            out.append(Finding(AMBER, f"{theme.capitalize()} noted in {len(years)} different years "
                                      f"({years[0]}–{years[-1]}).", THEME_QUESTIONS.get(theme)))
    return out


def check_latest_advisories(vehicle, advert, today):
    if not vehicle.tests:
        return []
    latest = vehicle.tests[-1]
    notes = latest.defects_of("ADVISORY", "MINOR")
    if not notes:
        return [Finding(GREEN, f"No advisories at the latest MOT ({latest.date:%b %Y}).")] if latest.passed else []
    listed = "; ".join(d.text for d in notes[:4]) + ("; ..." if len(notes) > 4 else "")
    return [Finding(INFO, f"Latest MOT advisories ({latest.date:%b %Y}): {listed}",
                    "Have the advisories from the last MOT been sorted?")]


def check_advert_text(vehicle, advert, today):
    out, text = [], advert.text.lower()
    if not text:
        return out
    for severity, pattern, message in ADVERT_FLAGS:
        if re.search(pattern, text):
            out.append(Finding(severity, f"Advert text: {message}."))
    return out


def check_model_notes(vehicle, advert, today):
    """Known weak spots for the model; amber when the car's own MOT history shows the symptom."""
    from assess.model_notes import notes_for
    label, notes = notes_for(vehicle)
    out = []
    for note in notes:
        years = sorted({t.date.year for t in vehicle.tests for d in t.defects
                        if note.mot and re.search(note.mot, d.text, re.I)})
        if years:
            seen = f"{years[0]}" if len(years) == 1 else f"{years[0]}–{years[-1]}"
            out.append(Finding(AMBER, f"{label} weak spot, and this car's MOT history shows signs of it ({seen}): "
                                      f"{note.text}", note.question))
        else:
            out.append(Finding(INFO, f"{label} weak spot: {note.text}", note.question))
    return out


CHECKS = [check_too_new, check_recall, check_identity, check_mot_status, check_mileage, check_gaps,
          check_failures, check_recurring, check_latest_advisories, check_advert_text, check_model_notes]


def run_checks(vehicle, advert, today=None):
    today = today or date.today()
    findings = [f for check in CHECKS for f in check(vehicle, advert, today)]
    return sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity])
