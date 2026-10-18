"""Normalised view of a DVSA MOT history API response.

The API returns one of two shapes (see the OpenAPI spec, "VehicleWithMotResponse" and
"NewRegVehicleResponse"): a vehicle with `motTests`, or a newly registered vehicle with
`motTestDueDate` and no tests. Both become a Vehicle; tests are sorted oldest first.
"""
from dataclasses import dataclass, field
from datetime import date, datetime

KM_TO_MILES = 0.621371


@dataclass
class Defect:
    type: str          # ADVISORY, MINOR, MAJOR, DANGEROUS, FAIL, ...
    text: str
    dangerous: bool = False


@dataclass
class MotTest:
    date: date
    passed: bool
    expiry: date | None
    miles: int | None  # None when the odometer was not read; km converted to miles
    odometer: str      # READ, UNREADABLE, NO_ODOMETER
    defects: list = field(default_factory=list)

    def defects_of(self, *types):
        return [d for d in self.defects if d.type in types]


@dataclass
class Vehicle:
    registration: str
    make: str
    model: str
    colour: str | None
    fuel: str | None
    engine_cc: int | None
    first_registered: date | None
    recall: str                 # Yes / No / Unknown / Unavailable
    first_mot_due: date | None  # only for vehicles too new to have had an MOT
    tests: list = field(default_factory=list)

    @property
    def year(self):
        return self.first_registered.year if self.first_registered else None

    @property
    def readings(self):
        """[(date, miles)] for tests where the odometer was read, oldest first."""
        return [(t.date, t.miles) for t in self.tests if t.miles is not None]


def _date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _miles(test):
    if test.get("odometerResultType") != "READ" or not test.get("odometerValue"):
        return None
    try:
        value = int(float(test["odometerValue"]))
    except ValueError:
        return None
    return round(value * KM_TO_MILES) if test.get("odometerUnit") == "KM" else value


def _name(value):
    """'PORSCHE' -> 'Porsche', 'MX-5' and 'GR86' stay as they are."""
    return " ".join(w.title() if w.isalpha() else w for w in (value or "").split())


def vehicle_from_api(payload):
    tests = []
    for t in payload.get("motTests") or []:
        when = _date(t.get("completedDate"))
        if not when:
            continue
        tests.append(MotTest(
            date=when,
            passed=t.get("testResult") == "PASSED",
            expiry=_date(t.get("expiryDate")),
            miles=_miles(t),
            odometer=t.get("odometerResultType") or "NO_ODOMETER",
            defects=[Defect(d.get("type") or "", (d.get("text") or "").strip(), bool(d.get("dangerous")))
                     for d in t.get("defects") or []],
        ))
    tests.sort(key=lambda t: t.date)
    engine = str(payload.get("engineSize") or "")
    return Vehicle(
        registration=payload.get("registration") or "",
        make=_name(payload.get("make")),
        model=_name(payload.get("model")),
        colour=payload.get("primaryColour"),
        fuel=payload.get("fuelType"),
        engine_cc=int(engine) if engine.isdigit() else None,
        first_registered=_date(payload.get("registrationDate")) or _date(payload.get("firstUsedDate")),
        recall=payload.get("hasOutstandingRecall") or "Unknown",
        first_mot_due=_date(payload.get("motTestDueDate")),
        tests=tests,
    )
