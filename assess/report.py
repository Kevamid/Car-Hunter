"""Plain-text report (works in a terminal and as a Telegram message)."""
from assess.checks import AMBER, GREEN, INFO, RED

ICONS = {RED: "🔴", AMBER: "🟠", GREEN: "🟢", INFO: "ℹ️"}
FOOTER = ("Based on DVSA MOT records. This does NOT check write-off, outstanding finance or "
          "stolen status: get a paid history check before buying.")


def verdict(findings):
    reds = sum(f.severity == RED for f in findings)
    ambers = sum(f.severity == AMBER for f in findings)
    if reds:
        return f"🔴 {reds} serious concern{'s' * (reds > 1)}: get answers before travelling to view"
    if ambers:
        return f"🟠 Worth viewing: ask about {ambers} thing{'s' * (ambers > 1)}"
    return "🟢 Nothing concerning in the official records"


def build_report(vehicle, advert, findings):
    lines = [f"🚗 {vehicle.year or ''} {vehicle.make} {vehicle.model} · {vehicle.registration}".replace("  ", " ")]
    facts = [vehicle.colour, vehicle.fuel, f"{vehicle.engine_cc}cc" if vehicle.engine_cc else None,
             f"first registered {vehicle.first_registered:%b %Y}" if vehicle.first_registered else None]
    lines.append(" · ".join(x for x in facts if x))
    claims = [f"£{advert.price:,}" if advert.price else None,
              f"{advert.mileage:,} miles" if advert.mileage is not None else None]
    if any(claims):
        lines.append("Advert: " + " · ".join(x for x in claims if x))
    lines += ["", f"Verdict: {verdict(findings)}", ""]
    lines += [f"{ICONS[f.severity]} {f.message}" for f in findings]

    questions = list(dict.fromkeys(f.question for f in findings if f.question and f.severity in (RED, AMBER, INFO)))
    if questions:
        lines += ["", "Ask the seller:"] + [f"• {q}" for q in questions]

    if vehicle.tests:
        span = f"{vehicle.tests[0].date.year}–{vehicle.tests[-1].date.year}"
        lines += ["", f"{len(vehicle.tests)} MOT tests on record ({span}). {FOOTER}"]
    else:
        lines += ["", FOOTER]
    return "\n".join(lines)
