"""Known weak spots for the models Kev is hunting, from reputable buying guides.

A note can be limited by first-registration year and engine size. If `mot` (a regex) matches
the car's MOT defect text, the note is raised to amber as "seen on this car's MOT history".
Keep notes short, factual and sourced; review them before adding to this list.
"""
import re
from dataclasses import dataclass


@dataclass
class Note:
    text: str
    question: str
    source: str
    years: tuple = (0, 9999)      # first-registration year range, inclusive
    cc: tuple = (0, 99999)        # engine size range in cc, inclusive
    mot: str | None = None        # MOT defect regex that corroborates the note


@dataclass
class Model:
    label: str
    make: str                     # regex on the MOT record's make
    model: str                    # regex on the MOT record's model
    notes: list
    cc: tuple = (0, 99999)        # restrict the whole model (e.g. Clio RS = 2.0 litre)

    def matches(self, vehicle):
        cc = vehicle.engine_cc or 0
        return (re.search(self.make, vehicle.make, re.I) and re.search(self.model, vehicle.model, re.I)
                and (not vehicle.engine_cc or self.cc[0] <= cc <= self.cc[1]))


OIL_LEAK = r"oil leak|leaking oil"
CORROSION = r"corro|rust"

MODELS = [
    Model("Porsche Cayman (987)", r"porsche", r"cayman", [
        Note("IMS bearing: 2006–08 cars have the larger, non-serviceable bearing. Failures are rare but "
             "wreck the engine; 2009-on (987.2) engines have no IMS.",
             "Is there any record of the IMS bearing being inspected or upgraded?",
             "Porsche Club GB 987 buyer's guide; LN Engineering", years=(2005, 2008)),
        Note("Bore scoring mainly affects early 3.4 S engines. Signs: high oil use, sooty tailpipes, a "
             "ticking noise, smoke on a cold start.",
             "Has a borescope inspection been done? How much oil does it use?",
             "Porsche Club GB 987 buyer's guide; Design911", years=(2005, 2008), cc=(3300, 3500),
             mot=r"smok|emission"),
        Note("Rear main seal leaks are common and can contaminate the clutch.",
             "Any oil leak between the engine and gearbox? Has the rear main seal been replaced?",
             "Design911 987 buyer's guide", years=(2005, 2012), mot=OIL_LEAK),
        Note("Radiators and water pumps can corrode or leak, especially with debris in the side intakes.",
             "When was the water pump last replaced? Are the front radiators clean?",
             "Design911 987 buyer's guide", years=(2005, 2012)),
    ]),
    Model("Toyota GT86 / Subaru BRZ", r"toyota|subaru", r"gt ?86|brz", [
        Note("The engine can use oil: topping up every 1,000–2,000 miles is not unusual.",
             "How often does it need topping up with oil?", "Autocar GT86 buying guide"),
        Note("Oil seepage is either the cam blanking plate (simple gasket) or the timing cover (an "
             "engine-out job).",
             "Where is any oil leak coming from: the cam plate or the timing cover?",
             "Autocar GT86 buying guide", mot=OIL_LEAK),
        Note("Second gear can be hard to select; usually a fluid change or clutch adjustment.",
             "Does it go into second gear cleanly from cold?", "PistonHeads GT86/BRZ buying guide"),
    ]),
    Model("Mazda MX-5 (NA/NB)", r"mazda", r"mx-?5|eunos", [
        Note("Rust is the main issue: rear sills, rear arches, floors and the front chassis rails, which "
             "are double-skinned and can rot from the inside while looking clean.",
             "Can you send photos of the sills, rear arches and front chassis rails? Any welding done?",
             "Goodwood MX-5 NA and NB buyer's guides; MX-5 Owners Club", years=(1989, 2005), mot=CORROSION),
        Note("Blocked hood drains let water into the sills and boot.",
             "Are the hood drains clear? Any damp in the cabin or boot?",
             "Goodwood MX-5 NA buyer's guide", years=(1989, 2005)),
        Note("The engine has a cambelt; check it has been changed on time.",
             "When was the cambelt last changed? Is there a receipt?",
             "Goodwood MX-5 NA buyer's guide", years=(1989, 2005)),
    ]),
    Model("Renaultsport Clio 2.0 (172/182/197/200)", r"renault", r"clio", cc=(1990, 2010), notes=[
        Note("Cambelt and auxiliary belt (ideally with water pump) every 72,000 miles or 5–6 years; "
             "about £400–600 at a specialist.",
             "When were the cambelt and auxiliary belt last changed? Is there a receipt?",
             "PistonHeads Clio 197/200 buying guide; clio197.net"),
        Note("Lower ball joints commonly wear.", "Have the lower ball joints been replaced?",
             "PistonHeads Clio 197/200 buying guide", years=(2006, 2013), mot=r"ball joint"),
        Note("Gearbox synchros can fail; listen for crunching changes.",
             "Any crunching or baulking going into gears?", "PistonHeads Clio 197/200 buying guide",
             years=(2006, 2013)),
        Note("Cracked exhaust manifolds and leaking flexi-pipes are known and costly.",
             "Any exhaust blowing or rattles from the manifold?", "PistonHeads Clio 197/200 buying guide",
             years=(2006, 2013), mot=r"exhaust"),
        Note("Noisy dephaser pulley, tired engine mounts (knocking in 1st/2nd) and snapped rear springs "
             "are common on the 172/182.",
             "Has the dephaser been replaced? Any knocking pulling away?", "ClioSport forum guidance",
             years=(2000, 2006), mot=r"spring"),
    ]),
    Model("BMW M140i (B58)", r"bmw", r"m ?140", [
        Note("Valve cover gasket and oil filter housing leaks are known, from around 70,000 miles.",
             "Any oil leaks around the valve cover or oil filter housing?",
             "Fast Car M140i buyer's guide", mot=OIL_LEAK),
        Note("The B58 can slowly lose coolant without an obvious leak.",
             "Does it need coolant topping up between services?", "Fast Car M140i buyer's guide"),
        Note("Many have been remapped, tracked or modified, sometimes returned to standard before sale.",
             "Has it been remapped or modified? Is it insured as such?", "Fast Car M140i buyer's guide"),
        Note("The automatic gearbox wants a fluid change at around 60,000 miles.",
             "If it's an automatic, has the gearbox oil been changed?", "Fast Car M140i buyer's guide"),
    ]),
]


def notes_for(vehicle):
    """(model label, [applicable notes]) for a vehicle, or (None, [])."""
    year, cc = vehicle.year or 0, vehicle.engine_cc
    for model in MODELS:
        if model.matches(vehicle):
            notes = [n for n in model.notes
                     if n.years[0] <= year <= n.years[1] and (cc is None or n.cc[0] <= cc <= n.cc[1])]
            return model.label, notes
    return None, []
