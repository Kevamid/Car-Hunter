"""One entry point for "assess this": used by the command line and the Telegram bot."""
from assess.checks import run_checks
from assess.mot import MotApiError, MotClient, normalise_registration
from assess.parse import parse_advert
from assess.report import build_report
from assess.vehicle import vehicle_from_api

HELP = ("Send me a registration, optionally with what the advert claims, e.g.\n"
        "AB12CDE £8,995 74k miles 2009 silver\n"
        "or paste the whole advert text (it must include the registration).\n\n"
        "I check it against the official DVSA MOT records: mileage, MOT history, recalls and "
        "whether the advert matches the record.")


def api_lookup(registration, _client=[]):
    """Real MOT API lookup, cached for 24h in the project database."""
    from assess.store import cached_vehicle_payload  # needs the project database
    if not _client:
        _client.append(MotClient())
    return cached_vehicle_payload(normalise_registration(registration), _client[0].vehicle)


def assess_text(text, lookup=api_lookup, today=None):
    """Reply text for a free-text request (a registration and/or advert text)."""
    advert = parse_advert(text)
    if not advert.registration:
        return "I couldn't find a registration in that.\n\n" + HELP
    try:
        payload = lookup(advert.registration)
    except MotApiError as exc:
        if exc.code in ("MOTH-RL-01", "MOTH-RL-02"):
            return "The MOT service is rate-limiting me. Try again in a few minutes."
        return f"The MOT service returned an error ({exc.code or exc.status}). Try again later."
    if payload is None:
        return f"No MOT record found for {advert.registration}. Check the registration and try again."
    vehicle = vehicle_from_api(payload)
    return build_report(vehicle, advert, run_checks(vehicle, advert, today=today))
