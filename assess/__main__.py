"""Command line: assess a car against its MOT record.

    python -m assess AB12CDE --price 8995 --miles 74000 --year 2009 --colour silver
    python -m assess --text advert.txt            # pasted advert (or "-" for stdin)
    python -m assess --sample assess/samples/sample_cayman_concerns.json --miles 74000
"""
import argparse
import json
import sys

from dotenv import load_dotenv

from assess.checks import run_checks
from assess.mot import MotApiError, MotClient, normalise_registration
from assess.parse import parse_advert
from assess.report import build_report
from assess.vehicle import vehicle_from_api


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m assess", description="Assess a car advert against DVSA MOT records.")
    parser.add_argument("registration", nargs="?", help="registration number, e.g. AB12CDE")
    parser.add_argument("--price", type=int)
    parser.add_argument("--miles", type=int, dest="mileage")
    parser.add_argument("--year", type=int)
    parser.add_argument("--colour")
    parser.add_argument("--fuel")
    parser.add_argument("--engine", type=float, dest="engine_litres", help="engine size in litres, e.g. 2.0")
    parser.add_argument("--text", help="file containing the advert text, or - for stdin")
    parser.add_argument("--sample", help="use a saved API response (JSON file) instead of calling the API")
    args = parser.parse_args(argv)

    text = ""
    if args.text:
        text = sys.stdin.read() if args.text == "-" else open(args.text, encoding="utf-8").read()
    advert = parse_advert(text, registration=args.registration, price=args.price, mileage=args.mileage,
                          year=args.year, colour=args.colour, fuel=args.fuel, engine_litres=args.engine_litres)

    if args.sample:
        with open(args.sample, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        if not advert.registration:
            parser.error("no registration given or found in the advert text")
        load_dotenv()
        if not MotClient.configured():
            parser.error("MOT API credentials missing (MOT_CLIENT_ID, MOT_CLIENT_SECRET, MOT_API_KEY, "
                         "MOT_TOKEN_URL in .env); use --sample to try a saved response")
        from assess.store import cached_vehicle_payload  # needs the project database
        try:
            payload = cached_vehicle_payload(normalise_registration(advert.registration), MotClient().vehicle)
        except MotApiError as exc:
            print(exc, file=sys.stderr)
            return 1
        if payload is None:
            print(f"No MOT record found for {advert.registration}. Check the registration.")
            return 1

    vehicle = vehicle_from_api(payload)
    print(build_report(vehicle, advert, run_checks(vehicle, advert)))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # emoji on Windows consoles
    sys.exit(main())
