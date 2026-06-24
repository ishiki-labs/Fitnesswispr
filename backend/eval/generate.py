"""Generate chatbot evaluation datasets.

Each sample is a JSON object:
    {
      "id": "register_phrasings-001",
      "category": "register_phrasings",
      "message": "can you please record bench 3x10 at 135",
      "expect": {
        "action": "register",          # register | clarify | answer
        "exercise": "bench press",     # canonical exercise name (lower)
        "sets": 3, "reps": 10, "weight": 135.0, "unit": "lbs"
      }
    }

Run:  python -m eval.generate
"""

from __future__ import annotations

import datetime
import json
import pathlib

DATASETS = pathlib.Path(__file__).parent / "datasets"


# --- Category 1: different ways of asking to register a fully-specified log ----

# (spoken_name, canonical_name, sets, reps, weight)
WORKOUTS = [
    ("bench press", "bench press", 3, 10, 135),
    ("squat", "squat", 5, 5, 225),
    ("overhead press", "overhead press", 3, 8, 95),
    ("barbell row", "barbell row", 4, 10, 135),
    ("deadlift", "deadlift", 3, 5, 315),
    ("bicep curl", "bicep curl", 3, 12, 30),
    ("leg press", "leg press", 3, 12, 300),
    ("lat pulldown", "lat pulldown", 3, 10, 120),
    ("incline dumbbell press", "incline dumbbell press", 3, 10, 70),
    ("tricep pushdown", "tricep pushdown", 3, 15, 50),
    ("romanian deadlift", "romanian deadlift", 4, 8, 185),
    ("dumbbell shoulder press", "dumbbell shoulder press", 3, 10, 45),
]

# Surface forms for rendering a workout spec into text.
SURFACES = [
    lambda n, s, r, w: f"{n} {s}x{r} at {w}",
    lambda n, s, r, w: f"{n} {s}x{r} at {w} lbs",
    lambda n, s, r, w: f"{n} {s} sets of {r} at {w}",
    lambda n, s, r, w: f"{n} {s} sets of {r} reps at {w} lbs",
    lambda n, s, r, w: f"{n} {s}x{r} @{w}",
    lambda n, s, r, w: f"{n} {s}x{r} {w}lbs",
]

# Ways people phrase "please record this". {w} = the rendered workout.
# Spans polite, imperative, casual, terse, rude, past-tense, and question-form.
WRAPPERS = [
    "{w}",
    "log {w}",
    "log this {w}",
    "log this: {w}",
    "please log {w}",
    "log {w} please",
    "can you log {w}",
    "can you log {w}?",
    "can you please log {w}",
    "could you log {w} for me",
    "could you please record {w}",
    "would you log {w}",
    "would you mind logging {w}",
    "will you log {w}",
    "will you record {w}?",
    "record {w}",
    "please record {w}",
    "record this {w}",
    "can you record {w}",
    "can you record this for me, {w}",
    "save {w}",
    "save this {w}",
    "add {w}",
    "add {w} to my log",
    "add this to my log: {w}",
    "put down {w}",
    "put {w} in",
    "note {w}",
    "make a note of {w}",
    "jot down {w}",
    "track {w}",
    "store {w}",
    "register {w}",
    "throw in {w}",
    "i did {w}",
    "i just did {w}",
    "just finished {w}",
    "i did {w}, log it",
    "i just did {w} can you record it",
    "hey log {w}",
    "yo log {w}",
    "hey can you log {w}",
    "ok so {w}, log that",
    "log it: {w}",
    "{w}, log it please",
    "{w} - record it",
    "for the record, {w}",
    "write down {w}",
    "today i did {w}",
    "mark down {w}",
    "you logging {w} or what",
    "log my workout: {w}",
    "can u log {w}",
    "pls log {w}",
    "plz record {w}",
    "hey can you please record this {w}",
    "log {w} for me will ya",
    "just log {w} already",
    "bro log {w}",
    "log {w} thanks",
]


def gen_register_phrasings(n: int = 100) -> list[dict]:
    samples: list[dict] = []
    for i in range(n):
        wrapper = WRAPPERS[i % len(WRAPPERS)]
        spoken, canon, sets, reps, weight = WORKOUTS[(i // 1) % len(WORKOUTS)]
        surface = SURFACES[(i // len(WORKOUTS)) % len(SURFACES)]
        rendered = surface(spoken, sets, reps, weight)
        message = wrapper.format(w=rendered)
        samples.append(
            {
                "id": f"register_phrasings-{i + 1:03d}",
                "category": "register_phrasings",
                "message": message,
                "expect": {
                    "action": "register",
                    "exercise": canon,
                    "sets": sets,
                    "reps": reps,
                    "weight": float(weight),
                    "unit": "lbs",
                },
            }
        )
    return samples


# Wrappers that are unambiguous logging requests (no question-form), reused by
# the missing-detail and rude categories.
LOG_WRAPPERS = [w for w in WRAPPERS if "?" not in w]


# --- Category 2: missing weight -> should ask a follow-up ---------------------

def gen_missing_weight(n: int = 100) -> list[dict]:
    # Weighted movements only (bodyweight wouldn't prompt for weight).
    moves = [w for w in WORKOUTS if w[0] not in ("bicep curl",)]
    surfaces = [
        lambda nm, s, r: f"{nm} {s}x{r}",
        lambda nm, s, r: f"{nm} {s} sets of {r}",
        lambda nm, s, r: f"{nm} {s} sets of {r} reps",
        lambda nm, s, r: f"{nm} {s}x{r} reps",
    ]
    out = []
    for i in range(n):
        wrapper = LOG_WRAPPERS[i % len(LOG_WRAPPERS)]
        spoken, canon, sets, reps, _ = moves[i % len(moves)]
        rendered = surfaces[(i // len(moves)) % len(surfaces)](spoken, sets, reps)
        out.append({
            "id": f"missing_weight-{i+1:03d}", "category": "missing_weight",
            "message": wrapper.format(w=rendered),
            "expect": {"action": "clarify", "field": "weight",
                       "exercise": canon, "sets": sets, "reps": reps},
        })
    return out


# --- Category 3: missing reps or sets -> should ask a follow-up ----------------

def gen_missing_reps_sets(n: int = 100) -> list[dict]:
    moves = [w for w in WORKOUTS if w[0] != "bicep curl"]
    out = []
    for i in range(n):
        wrapper = LOG_WRAPPERS[i % len(LOG_WRAPPERS)]
        spoken, canon, sets, reps, weight = moves[i % len(moves)]
        if i % 2 == 0:
            # weight present, reps missing, multiple sets -> asks reps
            rendered = f"{spoken} {sets} sets at {weight} lbs"
            field = "reps"
            exp = {"action": "clarify", "field": field, "exercise": canon, "sets": sets}
        else:
            # weight+reps present, single set -> asks how many sets
            rendered = f"{spoken} {reps} reps at {weight} lbs"
            field = "sets"
            exp = {"action": "clarify", "field": field, "exercise": canon, "reps": reps}
        out.append({
            "id": f"missing_reps_sets-{i+1:03d}", "category": "missing_reps_sets",
            "message": wrapper.format(w=rendered), "expect": exp,
        })
    return out


# --- Category 4: everything specified -> registers correctly (varied) ----------

VARIED_SINGLE = [
    ("incline bench press", "incline bench press", 4, 6, 155, "lbs"),
    ("front squat", "front squat", 5, 3, 185, "lbs"),
    ("dumbbell bench press", "dumbbell bench press", 3, 12, 60, "kg"),
    ("hack squat", "hack squat", 3, 10, 200, "lbs"),
    ("seated cable row", "seated cable row", 4, 12, 140, "lbs"),
    ("hammer curl", "hammer curl", 3, 12, 35, "lbs"),
    ("face pull", "face pull", 3, 20, 40, "lbs"),
    ("goblet squat", "goblet squat", 3, 15, 50, "lbs"),
    ("bulgarian split squat", "bulgarian split squat", 3, 8, 40, "lbs"),
    ("close grip bench press", "close grip bench press", 4, 8, 115, "lbs"),
    ("preacher curl", "preacher curl", 3, 10, 25, "kg"),
    ("pendlay row", "pendlay row", 5, 5, 155, "lbs"),
]

MULTI = [
    [("bench press", "bench press", 3, 10, 135), ("incline dumbbell press", "incline dumbbell press", 3, 10, 70), ("tricep pushdown", "tricep pushdown", 3, 12, 50)],
    [("squat", "squat", 5, 5, 225), ("romanian deadlift", "romanian deadlift", 3, 8, 185), ("leg press", "leg press", 3, 12, 300)],
    [("deadlift", "deadlift", 3, 5, 315), ("barbell row", "barbell row", 4, 8, 135), ("lat pulldown", "lat pulldown", 3, 12, 120)],
    [("overhead press", "overhead press", 4, 8, 95), ("lateral raise", "lateral raise", 3, 15, 20)],
    [("bench press", "bench press", 4, 6, 185), ("pull up", "pull up", 3, 8, None)],
]


def gen_fully_specified(n: int = 100) -> list[dict]:
    out = []
    for i in range(n):
        wrapper = LOG_WRAPPERS[i % len(LOG_WRAPPERS)]
        if i % 5 == 0:  # ~20% multi-exercise
            combo = MULTI[(i // 5) % len(MULTI)]
            parts = [f"{c[0]} {c[2]}x{c[3]}" + (f" at {c[4]}" if c[4] is not None else "") for c in combo]
            rendered = ", ".join(parts)
            specs = [{"exercise": c[1], "sets": c[2], "reps": c[3], "weight": (float(c[4]) if c[4] is not None else None)} for c in combo]
            exp = {"action": "register", "exercises": specs}
        else:
            spoken, canon, sets, reps, weight, unit = VARIED_SINGLE[i % len(VARIED_SINGLE)]
            surface = SURFACES[i % len(SURFACES)]
            rendered = surface(spoken, sets, reps, weight).replace(" lbs", f" {unit}")
            exp = {"action": "register", "exercise": canon, "sets": sets, "reps": reps, "weight": float(weight)}
        out.append({
            "id": f"fully_specified-{i+1:03d}", "category": "fully_specified",
            "message": wrapper.format(w=rendered), "expect": exp,
        })
    return out


# --- Category 5: rude / casual / typo'd phrasings (tone robustness) ------------

RUDE_WRAPPERS = [
    "just log {w} already",
    "just fucking log {w}",
    "yo dawg record {w}",
    "ugh can u just log {w} already",
    "LOG {w} NOW",
    "bro just record {w}",
    "log {w} k thanks",
    "log {w} pls and ty",
    "damn log {w}",
    "hey asshole log {w}",
    "log {w} you piece of junk",
    "for f sake just record {w}",
    "log {w}........",
    "lol log {w}",
    "ok cool log {w}",
    "{w}!!!! log it",
    "pleaseeee log {w}",
    "logg {w}",
    "rmember to log {w}",
    "can u plz record {w} ty",
]

TYPO_RENDER = [
    lambda n, s, r, w: f"{n} {s}x{r} at {w}",
    lambda n, s, r, w: f"{n} {s}x{r} @{w}",
    lambda n, s, r, w: f"{n} {s} x {r} {w}lbs",
    lambda n, s, r, w: f"{n} {s}x{r} {w}",
]

# (typo'd spoken form, canonical)
TYPO_MOVES = [
    ("benchh pres", "bench press", 3, 10, 135),
    ("sqaut", "squat", 5, 5, 225),
    ("deadlfit", "deadlift", 3, 5, 315),
    ("ohp", "overhead press", 3, 8, 95),
    ("incln db press", "incline dumbbell press", 3, 10, 70),
    ("tri pushdown", "tricep pushdown", 3, 15, 50),
    ("lat pulldwn", "lat pulldown", 3, 10, 120),
    ("barbel row", "barbell row", 4, 10, 135),
    ("legpress", "leg press", 3, 12, 300),
    ("bicep curls", "bicep curl", 3, 12, 30),
]


def gen_rude_scale(n: int = 100) -> list[dict]:
    out = []
    for i in range(n):
        wrapper = RUDE_WRAPPERS[i % len(RUDE_WRAPPERS)]
        spoken, canon, sets, reps, weight = TYPO_MOVES[i % len(TYPO_MOVES)]
        rendered = TYPO_RENDER[(i // len(TYPO_MOVES)) % len(TYPO_RENDER)](spoken, sets, reps, weight)
        out.append({
            "id": f"rude_scale-{i+1:03d}", "category": "rude_scale",
            "message": wrapper.format(w=rendered),
            "expect": {"action": "register", "exercise": canon, "sets": sets, "reps": reps, "weight": float(weight)},
        })
    return out


# --- Category 6: Q&A about history -> answered, not logged ---------------------

QA_TEMPLATES = [
    "what's my pr on {e}",
    "what is my best {e}",
    "how much did i {e} last time",
    "how much do i usually {e}",
    "what did i do last time on {e}",
    "am i making progress on {e}",
    "how's my {e} trending",
    "how many reps did i hit on {e} last session",
    "show me my {e} history",
    "when did i last do {e}",
    "have i been improving on {e}",
    "what's my heaviest {e}",
    "did i {e} this week",
    "how often do i train {e}",
    "summarize my {e} progress",
    "compare my {e} this month vs last month",
    "what's my average reps on {e}",
    "tell me my {e} numbers",
]

QA_EXERCISES = ["bench", "squat", "deadlift", "overhead press", "row", "curls"]


def gen_qa_lasttime(n: int = 100) -> list[dict]:
    out = []
    for i in range(n):
        tmpl = QA_TEMPLATES[i % len(QA_TEMPLATES)]
        ex = QA_EXERCISES[(i // len(QA_TEMPLATES)) % len(QA_EXERCISES)]
        msg = tmpl.format(e=ex)
        if i % 3 == 0:
            msg += "?"
        out.append({
            "id": f"qa_lasttime-{i+1:03d}", "category": "qa_lasttime",
            "message": msg, "expect": {"action": "answer"},
        })
    return out


# --- Category 7: cardio entries -----------------------------------------------

CARDIO = [
    ("ran {d} miles", "Running", "run"),
    ("i ran {d} miles", "Running", "run"),
    ("went for a {d} mile run", "Running", "run"),
    ("{d} mile jog", "Running", "run"),
    ("ran {d}k", "Running", "run"),
    ("treadmill for {m} minutes", "Treadmill", "treadmill"),
    ("{m} minute run", "Running", "run"),
    ("cycled {d} miles", "Cycling", "cycl"),
    ("biked for {m} minutes", "Cycling", "cycl"),
    ("rowed for {m} minutes", "Rowing", "row"),
    ("did a {m} min hiit session", "HIIT", "hiit"),
    ("sprints 10x100m", "Sprints", "sprint"),
    ("did sprints, 8x200m", "Sprints", "sprint"),
    ("walked {d} miles", "Walking", "walk"),
    ("swam for {m} minutes", "Swimming", "swim"),
]


def gen_cardio(n: int = 100) -> list[dict]:
    out = []
    wrappers = ["{w}", "log {w}", "record {w}", "can you log {w}", "please log {w}",
                "i did {w}", "just finished {w}", "add {w}"]
    for i in range(n):
        tmpl, activity, token = CARDIO[i % len(CARDIO)]
        dist = [2, 3, 5, 4, 6][i % 5]
        mins = [20, 30, 45, 15, 25][i % 5]
        rendered = tmpl.format(d=dist, m=mins)
        wrapper = wrappers[(i // len(CARDIO)) % len(wrappers)]
        out.append({
            "id": f"cardio-{i+1:03d}", "category": "cardio",
            "message": wrapper.format(w=rendered),
            "expect": {"action": "register_cardio", "activity": activity},
        })
    return out


# --- Category 8: tough real-world failures (from TestFlight) -------------------
# Modeled on the cases the user actually broke in the shipped build:
#   - bodyweight / plyometric moves "with body weight" (a 500 + weight nag),
#   - timed holds ("three sets of one minute plank"),
#   - spelled-out numbers from voice ("three sets twelve reps ... hundred lb"),
#   - ASR-garbled exercise names ("sludge push", "landline cleaning").

_NUM = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve",
    15: "fifteen", 20: "twenty", 25: "twenty five", 30: "thirty",
    45: "forty five", 50: "fifty", 95: "ninety five", 100: "a hundred",
}


def _num(x: int, spell: bool) -> str:
    return _NUM[x] if (spell and x in _NUM) else str(x)


TOUGH_WRAPPERS = [
    "can you please record {w}",
    "can you record {w}",
    "record {w}",
    "log {w}",
    "please log {w}",
    "hey can you log {w}",
    "i did {w}",
    "just did {w}",
    "add {w}",
    "yo log {w}",
]

# (spoken_move, distinctive canonical token expected in the parsed name)
BW_MOVES = [
    ("box jumps", "box jump"), ("burpees", "burpee"),
    ("jump squats", "jump squat"), ("sit ups", "sit up"),
    ("mountain climbers", "mountain climber"), ("pull ups", "pull up"),
    ("push ups", "push up"), ("dips", "dip"), ("pistol squats", "pistol squat"),
    ("broad jumps", "broad jump"), ("leg raises", "leg raise"),
    ("glute bridges", "glute bridge"),
]

HOLD_MOVES = [
    ("elbow plank", "plank"), ("plank", "plank"), ("side plank", "plank"),
    ("forearm plank", "plank"), ("wall sit", "wall sit"),
    ("dead hang", "hang"), ("hollow hold", "hollow"),
]

SPELLED_WEIGHTED = [
    ("bench press", "bench press", 3, 12, 135),
    ("squat", "squat", 5, 5, 225),
    ("deadlift", "deadlift", 3, 5, 315),
    ("overhead press", "overhead press", 3, 8, 95),
    ("barbell row", "barbell row", 4, 10, 135),
    ("leg press", "leg press", 3, 12, 300),
]

# Genuinely ASR-garbled spoken forms -> distinctive canonical token + full info.
GARBLED = [
    ("sled push and pull", "sled", 3, 10, 90),
    ("romanian deadlift", "romanian", 4, 8, 185),
    ("lat pull down", "pulldown", 3, 10, 120),
    ("face pulls", "face", 3, 20, 40),
    ("kettlebell swings", "swing", 3, 15, 53),
    ("goblet squat", "goblet", 3, 12, 50),
    ("hip thrust", "thrust", 3, 12, 225),
]

# The exact transcripts the user broke in TestFlight (verbatim).
VERBATIM = [
    ("Can you please record three sets 12 reps of box jumps with body weight",
     {"action": "register", "exercise": "box jump", "sets": 3, "reps": 12}),
    ("Can you please record three sets of one minute elbow plank",
     {"action": "register", "exercise": "plank", "sets": 3}),
    ("Can you please record three sets of one rep of sludge push and pull with hundred LB",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 1, "weight": 100.0}),
    ("Can you please record three sets 12 apps of landline cleaning cleaning",
     {"action": "clarify", "field": "weight", "exercise": "landmine"}),
]


def gen_tough_failures(n: int = 100) -> list[dict]:
    out: list[dict] = []

    def add(message: str, expect: dict) -> None:
        idx = len(out) + 1
        out.append({"id": f"tough_failures-{idx:03d}", "category": "tough_failures",
                    "message": message, "expect": expect})

    # A) bodyweight / plyometric "with body weight" -> register, no weight prompt (30)
    for i in range(30):
        spoken, canon = BW_MOVES[i % len(BW_MOVES)]
        spell = i % 2 == 0
        sets, reps = [3, 4, 3, 5][i % 4], [10, 12, 15, 20][i % 4]
        tail = ["with body weight", "bodyweight", "with bodyweight", ""][i % 4]
        rendered = f"{_num(sets, spell)} sets of {_num(reps, spell)} reps of {spoken} {tail}".strip()
        add(TOUGH_WRAPPERS[i % len(TOUGH_WRAPPERS)].format(w=rendered),
            {"action": "register", "exercise": canon, "sets": sets, "reps": reps})

    # B) timed holds -> register (duration short-circuits any follow-up) (20)
    for i in range(20):
        spoken, canon = HOLD_MOVES[i % len(HOLD_MOVES)]
        spell = i % 2 == 0
        sets = [3, 3, 4, 2][i % 4]
        dur = ["one minute", "30 seconds", "45 second", "two minutes",
               "ninety seconds"][i % 5]
        rendered = f"{_num(sets, spell)} sets of {dur} {spoken}"
        add(TOUGH_WRAPPERS[i % len(TOUGH_WRAPPERS)].format(w=rendered),
            {"action": "register", "exercise": canon, "sets": sets})

    # C) spelled-out numbers, weighted -> register (routing on word-numbers) (25)
    for i in range(25):
        spoken, canon, sets, reps, weight = SPELLED_WEIGHTED[i % len(SPELLED_WEIGHTED)]
        rendered = f"{_num(sets, True)} sets of {_num(reps, True)} reps of {spoken} at {weight}"
        if i % 3 == 0:  # also spell the weight when it's a round number
            rendered = f"{_num(sets, True)} sets of {_num(reps, True)} reps of {spoken} at {_num(weight, weight in _NUM)} pounds"
        add(TOUGH_WRAPPERS[i % len(TOUGH_WRAPPERS)].format(w=rendered),
            {"action": "register", "exercise": canon, "sets": sets, "reps": reps, "weight": float(weight)})

    # D) ASR-garbled names, fully specified -> register (21)
    for i in range(21):
        spoken, canon, sets, reps, weight = GARBLED[i % len(GARBLED)]
        spell = i % 2 == 0
        rendered = f"{spoken} {_num(sets, spell)} sets of {_num(reps, spell)} at {weight}"
        add(TOUGH_WRAPPERS[i % len(TOUGH_WRAPPERS)].format(w=rendered),
            {"action": "register", "exercise": canon, "sets": sets, "reps": reps, "weight": float(weight)})

    # E) verbatim transcripts the user broke (4)
    for message, expect in VERBATIM:
        add(message, expect)

    return out[:n] if n < len(out) else out


# --- Category 9: ASR-garbled novel names + spoken corrections -----------------
# From TestFlight: "sludge push and pull" (= sled push and pull) mis-logged as
# Lunge/Leg Press, and corrections ("X not Y", "no I meant ...") stored verbatim
# as the exercise name or hallucinated by the chat. `forbid` asserts the parsed
# name does NOT carry the wrong/negated movement.

GARBLED_CORRECTIONS = [
    # A) garbled "sled push and pull" — keyword survives, must NOT become Lunge/Leg.
    ("record three sets each of 12 reps with hundred lb sludge push and pull",
     {"action": "register", "exercise": "sled", "reps": 12, "weight": 100.0, "forbid": ["lunge", "leg"]}),
    ("can you record sludge push and pull 3 sets of 10 at 90",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 10, "weight": 90.0, "forbid": ["lunge", "leg"]}),
    ("log sludge push and pull three sets of twelve at hundred pounds",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 12, "weight": 100.0, "forbid": ["lunge", "leg"]}),
    ("sledge push and pull 4x8 at 70",
     {"action": "register", "exercise": "sled", "sets": 4, "reps": 8, "weight": 70.0, "forbid": ["lunge", "leg"]}),
    ("slug push and pull 3 sets of 15 with 50 lb",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 15, "weight": 50.0, "forbid": ["lunge", "leg"]}),
    ("record sled push n pull 3x12 at 110",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 12, "weight": 110.0, "forbid": ["lunge", "leg"]}),
    ("please log sludge push and pull, three sets, twelve reps, one hundred pounds",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 12, "weight": 100.0, "forbid": ["lunge", "leg"]}),
    ("i did sludge push and pull 5 sets of 5 at 135",
     {"action": "register", "exercise": "sled", "sets": 5, "reps": 5, "weight": 135.0, "forbid": ["lunge", "leg"]}),
    ("record 3 sets of 12 sludge pushes and pulls at 100",
     {"action": "register", "exercise": "sled", "reps": 12, "weight": 100.0, "forbid": ["lunge", "leg"]}),
    ("yo log sludge push and pull 3x8 90lb",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 8, "weight": 90.0, "forbid": ["lunge", "leg"]}),
    ("farmer's carry not farmer's walk, 3 sets of 40 steps at 50",
     {"action": "register", "exercise": "farmer", "sets": 3, "weight": 50.0, "forbid": ["walk"]}),
    ("record sissy squat 3 sets of 15 bodyweight",
     {"action": "register", "exercise": "sissy", "sets": 3, "reps": 15, "forbid": ["lunge"]}),

    # B) corrections "X not Y" WITH numbers -> log X, never Y or the literal "not".
    ("sled push and pull not leg press, 3 sets of 1 at 100",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 1, "weight": 100.0, "forbid": ["leg", "not", "lunge"]}),
    ("bench press not bench machine, 3x10 at 135",
     {"action": "register", "exercise": "bench press", "sets": 3, "reps": 10, "weight": 135.0, "forbid": ["machine", "not"]}),
    ("that's romanian deadlift not regular deadlift, 4x8 at 185",
     {"action": "register", "exercise": "romanian deadlift", "sets": 4, "reps": 8, "weight": 185.0, "forbid": ["regular", "not"]}),
    ("incline press not flat bench, 3x10 at 50",
     {"action": "register", "exercise": "incline press", "sets": 3, "reps": 10, "weight": 50.0, "forbid": ["flat", "not"]}),
    ("i meant goblet squat not back squat, 3x12 at 50",
     {"action": "register", "exercise": "goblet squat", "sets": 3, "reps": 12, "weight": 50.0, "forbid": ["back", "not"]}),
    ("front squat not front raise, 5x3 at 185",
     {"action": "register", "exercise": "front squat", "sets": 5, "reps": 3, "weight": 185.0, "forbid": ["raise", "not"]}),
    ("overhead press not chest press, 3x8 at 95",
     {"action": "register", "exercise": "overhead press", "sets": 3, "reps": 8, "weight": 95.0, "forbid": ["chest", "not"]}),
    ("lat pulldown not lateral raise, 3x12 at 120",
     {"action": "register", "exercise": "lat pulldown", "sets": 3, "reps": 12, "weight": 120.0, "forbid": ["lateral", "raise", "not"]}),
    ("it's hip thrust not hip raise, 3x12 at 225",
     {"action": "register", "exercise": "hip thrust", "sets": 3, "reps": 12, "weight": 225.0, "forbid": ["raise", "not"]}),
    ("sled push and pull not lunges, 3 sets of 1 at 100",
     {"action": "register", "exercise": "sled", "sets": 3, "reps": 1, "weight": 100.0, "forbid": ["lunge", "not"]}),
    ("record close grip bench not wide grip, 4x8 at 115",
     {"action": "register", "exercise": "close grip bench", "sets": 4, "reps": 8, "weight": 115.0, "forbid": ["wide", "not"]}),
    ("kettlebell swing not kettlebell snatch, 3x15 at 53",
     {"action": "register", "exercise": "kettlebell swing", "sets": 3, "reps": 15, "weight": 53.0, "forbid": ["snatch", "not"]}),

    # C) garbled rep/count words ("one wrap"/"reputation"/"ribs" = reps).
    ("log bench press three sets of one wrap at 135",
     {"action": "register", "exercise": "bench press", "sets": 3, "reps": 1, "weight": 135.0}),
    ("squat three sets of one reputation at 225",
     {"action": "register", "exercise": "squat", "sets": 3, "reps": 1, "weight": 225.0}),
    ("deadlift 3 sets of one rip at 315",
     {"action": "register", "exercise": "deadlift", "sets": 3, "reps": 1, "weight": 315.0}),
    ("bench press 3 sets of twelve ribs at 135",
     {"action": "register", "exercise": "bench press", "sets": 3, "reps": 12, "weight": 135.0}),
    ("leg press three sets of fifteen wraps at 300",
     {"action": "register", "exercise": "leg press", "sets": 3, "reps": 15, "weight": 300.0}),
    ("overhead press 3 sets of eight reputations at 95",
     {"action": "register", "exercise": "overhead press", "sets": 3, "reps": 8, "weight": 95.0}),
    ("barbell row three sets of ten wraps at 135",
     {"action": "register", "exercise": "barbell row", "sets": 3, "reps": 10, "weight": 135.0}),
    ("lat pulldown three sets of one rep each at 120",
     {"action": "register", "exercise": "lat pulldown", "sets": 3, "reps": 1, "weight": 120.0}),
    ("incline press 3 sets of one rap at 50",
     {"action": "register", "exercise": "incline press", "sets": 3, "reps": 1, "weight": 50.0}),
    ("tricep pushdown three sets of twelve wraps at 50",
     {"action": "register", "exercise": "tricep pushdown", "sets": 3, "reps": 12, "weight": 50.0}),
]


def gen_garbled_corrections(n: int = 100) -> list[dict]:
    out = []
    for i, (message, expect) in enumerate(GARBLED_CORRECTIONS):
        out.append({"id": f"garbled_corrections-{i+1:03d}",
                    "category": "garbled_corrections",
                    "message": message, "expect": expect})
    return out


# ---------------------------------------------------------------------------
# Category 10: strength & conditioning catalog.
#
# "Can a real user add this exercise easily on the first try?" One natural,
# fully-specified first attempt per movement, spanning the whole S&C taxonomy:
# barbell, dumbbell, machine/cable, bodyweight/calisthenics, Olympic/power,
# kettlebell, strongman/carries, plyometrics, and conditioning/cardio.
#
# `exercise` lists the minimal distinctive movement tokens any correct name
# must contain; `forbid` guards against the common wrong mapping. Strength
# moves expect `register`; cardio expects `register_cardio`. Where a movement
# is naturally distance/time-based (carries, holds, intervals) the phrasing is
# left as a user would say it, so the run surfaces any reps/weight nags.
# ---------------------------------------------------------------------------
def _reg(exercise, **kw):
    d = {"action": "register", "exercise": exercise}
    d.update(kw)
    return d


def _cardio(activity):
    return {"action": "register_cardio", "activity": activity}


SC_CATALOG: list[tuple[str, dict]] = [
    # --- Barbell: lower ---
    ("back squat 5x5 at 225", _reg("squat", sets=5, reps=5, weight=225.0, forbid=["leg", "front"])),
    ("front squat 3x3 at 185", _reg("front squat", sets=3, reps=3, weight=185.0)),
    ("overhead squat 3x5 at 95", _reg("overhead squat", sets=3, reps=5, weight=95.0)),
    ("box squat 4x4 at 205", _reg("box squat", sets=4, reps=4, weight=205.0)),
    ("pause squat 3x5 at 185", _reg("pause squat", sets=3, reps=5, weight=185.0)),
    ("conventional deadlift 1x5 at 315", _reg("deadlift", reps=5, weight=315.0, forbid=["romanian", "stiff"])),
    ("sumo deadlift 3x5 at 275", _reg("sumo deadlift", sets=3, reps=5, weight=275.0)),
    ("romanian deadlift 4x8 at 185", _reg("romanian deadlift", sets=4, reps=8, weight=185.0)),
    ("stiff leg deadlift 3x10 at 135", _reg("stiff leg deadlift", sets=3, reps=10, weight=135.0)),
    ("deficit deadlift 3x3 at 295", _reg("deficit deadlift", sets=3, reps=3, weight=295.0)),
    ("barbell hip thrust 3x12 at 225", _reg("hip thrust", sets=3, reps=12, weight=225.0)),
    ("good morning 3x10 at 95", _reg("good morning", sets=3, reps=10, weight=95.0)),
    ("barbell walking lunge 3x10 at 95", _reg("lunge", sets=3, reps=10, weight=95.0)),
    ("bulgarian split squat 3x8 at 50", _reg("split squat", sets=3, reps=8, weight=50.0)),
    ("barbell glute bridge 3x12 at 185", _reg("glute bridge", sets=3, reps=12, weight=185.0)),
    ("zercher squat 3x6 at 155", _reg("zercher squat", sets=3, reps=6, weight=155.0)),
    # --- Barbell: upper push ---
    ("bench press 3x10 at 135", _reg("bench press", sets=3, reps=10, weight=135.0)),
    ("incline bench press 4x8 at 115", _reg("incline bench press", sets=4, reps=8, weight=115.0)),
    ("decline bench press 3x10 at 135", _reg("decline bench press", sets=3, reps=10, weight=135.0)),
    ("close grip bench press 3x8 at 115", _reg("close grip bench press", sets=3, reps=8, weight=115.0)),
    ("standing overhead press 5x5 at 95", _reg("overhead press", sets=5, reps=5, weight=95.0, forbid=["bench", "leg"])),
    ("push press 5x3 at 135", _reg("push press", sets=5, reps=3, weight=135.0)),
    ("barbell floor press 3x6 at 155", _reg("floor press", sets=3, reps=6, weight=155.0)),
    ("landmine press 3x10 at 45", _reg("landmine press", sets=3, reps=10, weight=45.0)),
    # --- Barbell: upper pull ---
    ("barbell row 4x8 at 135", _reg("row", sets=4, reps=8, weight=135.0, forbid=["leg"])),
    ("pendlay row 5x5 at 155", _reg("pendlay row", sets=5, reps=5, weight=155.0)),
    ("bent over row 4x10 at 115", _reg("bent over row", sets=4, reps=10, weight=115.0)),
    ("t-bar row 3x10 at 90", _reg("t-bar row", sets=3, reps=10, weight=90.0)),
    ("barbell shrug 3x15 at 225", _reg("shrug", sets=3, reps=15, weight=225.0)),
    ("barbell upright row 3x12 at 65", _reg("upright row", sets=3, reps=12, weight=65.0)),
    ("barbell curl 3x10 at 65", _reg("curl", sets=3, reps=10, weight=65.0)),
    # --- Dumbbell ---
    ("dumbbell bench press 3x10 at 60", _reg("bench press", sets=3, reps=10, weight=60.0)),
    ("incline dumbbell press 3x12 at 50", _reg("incline", sets=3, reps=12, weight=50.0)),
    ("dumbbell shoulder press 3x10 at 45", _reg("shoulder press", sets=3, reps=10, weight=45.0)),
    ("arnold press 3x12 at 35", _reg("arnold press", sets=3, reps=12, weight=35.0)),
    ("dumbbell lateral raise 4x15 at 20", _reg("lateral raise", sets=4, reps=15, weight=20.0)),
    ("dumbbell front raise 3x12 at 20", _reg("front raise", sets=3, reps=12, weight=20.0)),
    ("rear delt fly 3x15 at 15", _reg("rear delt", sets=3, reps=15, weight=15.0)),
    ("one arm dumbbell row 3x10 at 70", _reg("row", sets=3, reps=10, weight=70.0)),
    ("dumbbell curl 3x12 at 30", _reg("curl", sets=3, reps=12, weight=30.0)),
    ("hammer curl 3x12 at 35", _reg("hammer curl", sets=3, reps=12, weight=35.0)),
    ("incline dumbbell curl 3x10 at 25", _reg("curl", sets=3, reps=10, weight=25.0)),
    ("dumbbell fly 3x12 at 30", _reg("fly", sets=3, reps=12, weight=30.0)),
    ("dumbbell pullover 3x12 at 45", _reg("pullover", sets=3, reps=12, weight=45.0)),
    ("goblet squat 3x12 at 50", _reg("goblet squat", sets=3, reps=12, weight=50.0)),
    ("dumbbell romanian deadlift 3x10 at 50", _reg("romanian deadlift", sets=3, reps=10, weight=50.0)),
    ("dumbbell shrug 3x15 at 70", _reg("shrug", sets=3, reps=15, weight=70.0)),
    ("tricep kickback 3x15 at 15", _reg("kickback", sets=3, reps=15, weight=15.0)),
    ("dumbbell skull crusher 3x12 at 25", _reg("skull crusher", sets=3, reps=12, weight=25.0)),
    ("dumbbell thruster 3x10 at 35", _reg("thruster", sets=3, reps=10, weight=35.0)),
    ("dumbbell step up 3x10 at 40", _reg("step up", sets=3, reps=10, weight=40.0)),
    # --- Machine / cable ---
    ("lat pulldown 3x12 at 120", _reg("lat pulldown", sets=3, reps=12, weight=120.0)),
    ("seated cable row 3x12 at 130", _reg("row", sets=3, reps=12, weight=130.0)),
    ("chest press machine 3x12 at 100", _reg("chest press", sets=3, reps=12, weight=100.0)),
    ("pec deck 3x15 at 80", _reg("pec deck", sets=3, reps=15, weight=80.0, forbid=["leg"])),
    ("cable fly 3x15 at 25", _reg("fly", sets=3, reps=15, weight=25.0)),
    ("tricep pushdown 3x15 at 50", _reg("pushdown", sets=3, reps=15, weight=50.0)),
    ("rope pushdown 3x15 at 40", _reg("pushdown", sets=3, reps=15, weight=40.0)),
    ("cable curl 3x12 at 40", _reg("curl", sets=3, reps=12, weight=40.0)),
    ("face pull 3x20 at 35", _reg("face pull", sets=3, reps=20, weight=35.0)),
    ("leg press 3x12 at 360", _reg("leg press", sets=3, reps=12, weight=360.0)),
    ("leg extension 3x15 at 90", _reg("leg extension", sets=3, reps=15, weight=90.0)),
    ("lying leg curl 3x12 at 80", _reg("leg curl", sets=3, reps=12, weight=80.0)),
    ("seated leg curl 3x12 at 85", _reg("leg curl", sets=3, reps=12, weight=85.0)),
    ("hack squat 3x10 at 180", _reg("hack squat", sets=3, reps=10, weight=180.0)),
    ("seated calf raise 4x15 at 90", _reg("calf raise", sets=4, reps=15, weight=90.0)),
    ("cable crunch 3x15 at 60", _reg("crunch", sets=3, reps=15, weight=60.0)),
    ("hip abduction machine 3x20 at 100", _reg("abduction", sets=3, reps=20, weight=100.0)),
    ("hip adduction machine 3x20 at 100", _reg("adduction", sets=3, reps=20, weight=100.0)),
    ("smith machine squat 3x10 at 135", _reg("squat", sets=3, reps=10, weight=135.0)),
    # --- Bodyweight / calisthenics ---
    ("push ups 3x20", _reg("push up", sets=3, reps=20)),
    ("pull ups 4x8", _reg("pull up", sets=4, reps=8)),
    ("chin ups 3x10", _reg("chin up", sets=3, reps=10)),
    ("dips 3x12", _reg("dip", sets=3, reps=12)),
    ("pistol squats 3x8", _reg("pistol squat", sets=3, reps=8)),
    ("bodyweight squats 3x25", _reg("squat", sets=3, reps=25)),
    ("walking lunges 3x20", _reg("lunge", sets=3, reps=20)),
    ("hanging leg raises 3x15", _reg("leg raise", sets=3, reps=15)),
    ("sit ups 3x30", _reg("sit up", sets=3, reps=30)),
    ("crunches 3x25", _reg("crunch", sets=3, reps=25)),
    ("burpees 4x15", _reg("burpee", sets=4, reps=15)),
    ("mountain climbers 3x30", _reg("mountain climber", sets=3, reps=30)),
    ("nordic curls 3x6", _reg("nordic", sets=3, reps=6)),
    ("inverted rows 3x12", _reg("inverted row", sets=3, reps=12)),
    ("pike push ups 3x10", _reg("pike", sets=3, reps=10)),
    ("diamond push ups 3x15", _reg("diamond", sets=3, reps=15)),
    ("handstand push ups 3x5", _reg("handstand", sets=3, reps=5)),
    ("muscle ups 3x3", _reg("muscle up", sets=3, reps=3)),
    ("glute bridges 3x20", _reg("glute bridge", sets=3, reps=20)),
    ("plank for 60 seconds", _reg("plank")),
    ("side plank 3 sets of 45 seconds", _reg("side plank")),
    ("hollow hold 3 sets of 30 seconds", _reg("hollow")),
    ("wall sit for 90 seconds", _reg("wall sit")),
    ("dead hang for 60 seconds", _reg("dead hang")),
    ("box jumps 4x10", _reg("box jump", sets=4, reps=10)),
    ("broad jumps 3x8", _reg("broad jump", sets=3, reps=8)),
    ("jumping jacks 3x50", _reg("jumping jack", sets=3, reps=50)),
    # --- Olympic / power ---
    ("power clean 5x3 at 155", _reg("power clean", sets=5, reps=3, weight=155.0)),
    ("hang clean 4x3 at 135", _reg("hang clean", sets=4, reps=3, weight=135.0)),
    ("clean and jerk 5x2 at 165", _reg("clean", sets=5, reps=2, weight=165.0)),
    ("snatch 5x2 at 115", _reg("snatch", sets=5, reps=2, weight=115.0)),
    ("power snatch 4x2 at 95", _reg("power snatch", sets=4, reps=2, weight=95.0)),
    ("push jerk 4x3 at 145", _reg("jerk", sets=4, reps=3, weight=145.0)),
    ("split jerk 4x2 at 155", _reg("split jerk", sets=4, reps=2, weight=155.0)),
    ("barbell thruster 4x8 at 95", _reg("thruster", sets=4, reps=8, weight=95.0)),
    ("clean pull 3x3 at 185", _reg("clean pull", sets=3, reps=3, weight=185.0)),
    # --- Kettlebell ---
    ("kettlebell swing 4x20 at 53", _reg("swing", sets=4, reps=20, weight=53.0)),
    ("kettlebell goblet squat 3x12 at 35", _reg("goblet squat", sets=3, reps=12, weight=35.0)),
    ("kettlebell clean 3x10 at 35", _reg("clean", sets=3, reps=10, weight=35.0)),
    ("kettlebell snatch 3x8 at 35", _reg("snatch", sets=3, reps=8, weight=35.0)),
    ("turkish get up 3x5 at 35", _reg("get up", sets=3, reps=5, weight=35.0)),
    ("kettlebell deadlift 3x12 at 70", _reg("deadlift", sets=3, reps=12, weight=70.0)),
    ("kettlebell press 3x8 at 35", _reg("press", sets=3, reps=8, weight=35.0)),
    ("kettlebell windmill 3x8 at 25", _reg("windmill", sets=3, reps=8, weight=25.0)),
    # --- Strongman / carries / conditioning (often distance/time based) ---
    ("farmers carry 3 sets of 40 yards at 70 per hand", _reg("farmer", weight=70.0)),
    ("suitcase carry 3 sets of 30 yards at 60", _reg("suitcase", weight=60.0)),
    ("sled push 4 sets of 20 yards at 180", _reg("sled push", weight=180.0, forbid=["leg", "lunge"])),
    ("sled drag 3 sets of 25 yards at 140", _reg("sled", weight=140.0)),
    ("prowler push 4 sets of 20 meters at 200", _reg("prowler", weight=200.0)),
    ("yoke walk 3 sets of 15 meters at 400", _reg("yoke", weight=400.0)),
    ("atlas stone over bar 3x3 at 200", _reg("atlas stone", sets=3, reps=3, weight=200.0)),
    ("log press 4x3 at 145", _reg("log press", sets=4, reps=3, weight=145.0)),
    ("tire flips 3x5", _reg("tire flip", sets=3, reps=5)),
    ("sledgehammer swings 3x20", _reg("sledgehammer", sets=3, reps=20)),
    ("battle ropes 3 sets of 30 seconds", _reg("battle rope")),
    ("wall balls 3x20 at 20", _reg("wall ball", sets=3, reps=20, weight=20.0)),
    ("medicine ball slams 3x15 at 20", _reg("slam", sets=3, reps=15, weight=20.0)),
    ("devil press 3x10 at 35", _reg("devil press", sets=3, reps=10, weight=35.0)),
    ("sandbag carry 3 sets of 40 yards at 100", _reg("sandbag", weight=100.0)),
    ("kettlebell farmers carry 3 sets of 50 feet at 53", _reg("farmer", weight=53.0)),
    # --- Conditioning / cardio (register_cardio) ---
    ("ran 3 miles", _cardio("Running")),
    ("sprinted 10x100 meters", _cardio("Sprints")),
    ("rowed 2000 meters on the erg", _cardio("Rowing")),
    ("assault bike for 20 minutes", _cardio("Cycling")),
    ("echo bike 15 minute intervals", _cardio("Cycling")),
    ("ski erg 1000 meters", _cardio("Ski")),
    ("treadmill incline walk for 30 minutes", _cardio("Walking")),
    ("elliptical for 25 minutes", _cardio("Elliptical")),
    ("stair climber for 20 minutes", _cardio("Stair")),
    ("swam 1000 meters", _cardio("Swimming")),
    ("jumped rope for 10 minutes", _cardio("Jump Rope")),
    ("30 minute zone 2 run", _cardio("Running")),
]


def gen_sc_catalog() -> list[dict]:
    out = []
    for i, (message, expect) in enumerate(SC_CATALOG):
        out.append({"id": f"sc_catalog-{i+1:03d}", "category": "sc_catalog",
                    "message": message, "expect": expect})
    return out


# ---------------------------------------------------------------------------
# Category 11: relative / spoken dates ("yesterday", "last friday", "june 10th")
#
# Reproduces the reported bug: when the user says "I did bench yesterday" the
# entry is saved with TODAY's date. The parser never extracts a date and the iOS
# client always stamps the DatePicker (which defaults to Date() == today), so any
# past-date phrasing is wrong. Each sample fixes a reference "today" so the
# expected workout_date is reproducible no matter when the eval runs.
# ---------------------------------------------------------------------------

# Pinned reference "today". 2026-06-23 is a Tuesday.
REL_TODAY = datetime.date(2026, 6, 23)


def _iso(d: datetime.date) -> str:
    return d.isoformat()


def _off(days: int) -> str:
    return _iso(REL_TODAY - datetime.timedelta(days=days))


def _last_weekday(name: str) -> str:
    """Most recent past date (strictly before REL_TODAY) on the given weekday."""
    names = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    target = names.index(name)
    d = REL_TODAY - datetime.timedelta(days=1)
    while d.weekday() != target:
        d -= datetime.timedelta(days=1)
    return _iso(d)


# (spoken date phrase, expected workout_date)
REL_DATE_CASES: list[tuple[str, str]] = [
    ("yesterday", _off(1)),
    ("last night", _off(1)),
    ("today", _off(0)),
    ("this morning", _off(0)),
    ("earlier today", _off(0)),
    ("two days ago", _off(2)),
    ("the day before yesterday", _off(2)),
    ("three days ago", _off(3)),
    ("a couple days ago", _off(2)),
    ("a week ago", _off(7)),
    ("last monday", _last_weekday("monday")),
    ("last wednesday", _last_weekday("wednesday")),
    ("last thursday", _last_weekday("thursday")),
    ("last friday", _last_weekday("friday")),
    ("last saturday", _last_weekday("saturday")),
    ("last sunday", _last_weekday("sunday")),
    ("on june 10th", "2026-06-10"),
    ("on june 1st", "2026-06-01"),
    ("on the 15th", "2026-06-15"),
]

# Simple, unambiguous workouts so the only thing that can fail is the DATE.
REL_WORKOUTS: list[tuple[str, dict]] = [
    ("bench press 3x10 at 135", _reg("bench press", sets=3, reps=10, weight=135.0)),
    ("squat 5x5 at 225", _reg("squat", sets=5, reps=5, weight=225.0)),
    ("deadlift 3x5 at 315", _reg("deadlift", sets=3, reps=5, weight=315.0)),
    ("overhead press 3x8 at 95", _reg("overhead press", sets=3, reps=8, weight=95.0)),
]

# Surfaces that read naturally with both relative and explicit date phrases.
REL_SURFACES = [
    lambda w, p: f"{w} {p}",
    lambda w, p: f"log {w} {p}",
    lambda w, p: f"{p} i did {w}",
]


def gen_relative_dates() -> list[dict]:
    out: list[dict] = []
    n = 0

    def add(message: str, base: dict, exp_date: str) -> None:
        nonlocal n
        expect = dict(base)
        expect["workout_date"] = exp_date
        n += 1
        out.append({
            "id": f"relative_dates-{n:03d}",
            "category": "relative_dates",
            "today": _iso(REL_TODAY),
            "message": message,
            "expect": expect,
        })

    # No date mentioned at all → must default to today.
    for wi, (workout, base) in enumerate(REL_WORKOUTS):
        add(REL_SURFACES[wi % 2](workout, "").strip(), base, _off(0))

    # Spoken date phrases → resolved date.
    for ci, (phrase, exp_date) in enumerate(REL_DATE_CASES):
        for k in range(2):  # two phrasing variants per date phrase
            workout, base = REL_WORKOUTS[(ci + k) % len(REL_WORKOUTS)]
            surface = REL_SURFACES[(ci + k) % len(REL_SURFACES)]
            add(surface(workout, phrase), base, exp_date)

    return out


def write_dataset(name: str, samples: list[dict]) -> pathlib.Path:
    DATASETS.mkdir(parents=True, exist_ok=True)
    path = DATASETS / name
    with path.open("w") as f:
        for s in samples:
            f.write(json.dumps(s) + "\n")
    return path


DATASET_BUILDERS = {
    "01_register_phrasings.jsonl": lambda: gen_register_phrasings(100),
    "02_missing_weight.jsonl": lambda: gen_missing_weight(100),
    "03_missing_reps_sets.jsonl": lambda: gen_missing_reps_sets(100),
    "04_fully_specified.jsonl": lambda: gen_fully_specified(100),
    "05_rude_scale.jsonl": lambda: gen_rude_scale(100),
    "06_qa_lasttime.jsonl": lambda: gen_qa_lasttime(100),
    "07_cardio.jsonl": lambda: gen_cardio(100),
    "08_tough_failures.jsonl": lambda: gen_tough_failures(100),
    "09_garbled_corrections.jsonl": lambda: gen_garbled_corrections(),
    "10_sc_catalog.jsonl": lambda: gen_sc_catalog(),
    "11_relative_dates.jsonl": lambda: gen_relative_dates(),
}


if __name__ == "__main__":
    for name, builder in DATASET_BUILDERS.items():
        path = write_dataset(name, builder())
        print(f"wrote {path} ({sum(1 for _ in path.open())} samples)")
