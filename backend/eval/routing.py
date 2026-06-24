"""Faithful Python port of the iOS client's chat routing logic.

The decision of whether a chat message gets *logged as a workout* or *answered
as a question* happens entirely on the client today, in
``ios/.../Assistant/AssistantViewModel.swift``. To benchmark the real end-to-end
behaviour (not just the parser), the eval has to reproduce that decision here.

Keep these functions in lock-step with the Swift implementation. If/when routing
moves server-side, this module should call the server instead of re-deriving it.
"""

from __future__ import annotations

import re

# --- looksLikeQuestion (AssistantViewModel.looksLikeQuestion) -----------------

_QUESTION_PREFIXES = [
    "what", "when", "how", "why", "who", "which", "whats", "what's",
    "did i", "do i", "have i", "show", "tell", "can you", "should i",
    "explain", "summarize", "compare", "am i", "is my", "are my",
]

_QUESTION_KEYWORDS = [
    "my pr", "personal record", " pr ", "last time", "how much",
    "how many", "progress on", "best ", "average", "trend",
]

# A *history* question asks about past data, so it must be answered even when it
# also mentions an exercise + number (e.g. "what's my one rep max on bench"). It
# takes precedence over loggable content in the routing gate.
_HISTORY_KEYWORDS = [
    "my pr", "personal record", " pr ", "1rm", "one rep max", "rep max",
    "last time", "how much", "how many", "how often", "progress", "best ",
    "average", "trend", "heaviest", "what's my", "what is my", "whats my",
    "how's my", "hows my", "compared to", "since last", "do i usually",
]

# Spelled-out numbers — voice transcription often writes "three sets twelve
# reps" rather than "3 sets 12 reps", which the digit-only guards used to miss.
_NUMBER_WORDS = {
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
    "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty", "fifty",
    "sixty", "seventy", "eighty", "ninety", "hundred", "couple", "few", "dozen",
}

_WORD_RE = re.compile(r"[a-z]+")


def _has_number(t: str) -> bool:
    if any(c.isdigit() for c in t):
        return True
    return any(w in _NUMBER_WORDS for w in _WORD_RE.findall(t))


def looks_like_question(text: str) -> bool:
    t = text.lower().strip()
    if t.endswith("?"):
        return True
    if any(t.startswith(p) for p in _QUESTION_PREFIXES):
        return True
    return any(k in t for k in _QUESTION_KEYWORDS)


def looks_like_history_question(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in _HISTORY_KEYWORDS)


# --- looksLikeWorkout (AssistantViewModel.looksLikeWorkout) -------------------

_WORKOUT_SIGNALS = [
    "rep", "set", "lbs", " lb", "kg", "pound", "kilo", "@",
    # timed holds ("45 second plank", "one minute wall sit", "hold for 30s")
    "second", "minute", "hold",
]
_NxN = re.compile(r"\d\s*[x×]\s*\d")


def looks_like_workout(text: str) -> bool:
    t = text.lower()
    if not _has_number(t):
        return False
    if any(s in t for s in _WORKOUT_SIGNALS):
        return True
    return _NxN.search(t) is not None


# --- looksLikeCardio (AssistantViewModel.looksLikeCardio) ---------------------
# Cardio entries ("ran 3 miles", "30 minute run") carry no set/rep/weight
# signals, so looks_like_workout misses them. Used only for the routing gate so
# a polite request ("can you log ran 2 miles") still gets logged.

_CARDIO_SIGNALS = [
    "ran ", "run", "running", "jog", "sprint", "treadmill", "cycl", "bike",
    "biked", "row", "swam", "swim", "walk", "hiit", "cardio", "mile", "km",
    "marathon", "elliptical",
]


def looks_like_cardio(text: str) -> bool:
    t = text.lower()
    if not _has_number(t):
        return False
    return any(s in t for s in _CARDIO_SIGNALS)


# --- isBodyweight (AssistantViewModel.isBodyweight) ---------------------------

_BODYWEIGHT = [
    "push up", "pushup", "push-up", "pull up", "pullup", "pull-up",
    "chin up", "chinup", "dip", "plank", "sit up", "situp", "sit-up",
    "crunch", "burpee", "mountain climber", "leg raise", "knee raise",
    "hanging", "box jump", "jump squat", "squat jump", "jumping jack",
    "broad jump", "tuck jump", "lunge jump", "split jump", "star jump",
    "wall sit", "glute bridge", "hip bridge", "superman", "bird dog",
    "flutter kick", "v-up", "v up", "hollow", "l-sit", "l sit", "dead hang",
    "pistol squat", "sissy squat", "air squat", "bodyweight", "body weight", "high knee",
    "bear crawl", "inchworm", "handstand", "skater", "toes to bar",
    "calf raise", "lunge", "inverted row", "tire flip", "sledgehammer",
    "nordic", "pike push", "muscle up", "ring row", "ring dip",
]


def is_bodyweight(name: str) -> bool:
    n = name.lower()
    return any(b in n for b in _BODYWEIGHT)


# --- isDistanceBased (AssistantViewModel.isDistanceBased) ---------------------
# Carries / sleds / drags / yoke walks are load + distance, not reps.
_DISTANCE_BASED = [
    "carry", "carries", "farmer", "suitcase", "sled", "drag", "prowler",
    "yoke", "sandbag",
]


def is_distance_based(name: str) -> bool:
    n = name.lower()
    return any(b in n for b in _DISTANCE_BASED)


# --- messageStatesSetCount (AssistantViewModel.messageStatesSetCount) ---------
_SET_COUNT_RE = re.compile(
    r"\b\d+\s*[x×]\s*\d+"
    r"|\b\d+\s*sets?\b"
    r"|\b(?:one|two|three|four|five|six|seven|eight|nine|ten|single|couple|few)\s+sets?\b"
)


def states_set_count(message: str) -> bool:
    t = message.lower()
    if "single set" in t or "one set" in t:
        return True
    return _SET_COUNT_RE.search(t) is not None


# --- firstMissingField (AssistantViewModel.firstMissingField) -----------------
# NOTE: the real implementation can also ask for a `.variant` based on the user's
# history. The eval runs against an empty history context, where variant
# suggestions are always empty, so we omit that branch here.

def first_missing_field(exercise: dict, message: str = "") -> str | None:
    sets = exercise.get("sets") or []
    if not sets:
        return None
    if any(s.get("duration_seconds") is not None for s in sets):
        return None  # timed hold
    has_weight = any(s.get("weight") is not None for s in sets)
    has_reps = any(s.get("reps") is not None for s in sets)
    equipment = (exercise.get("equipment") or "").lower()
    bodyweight = equipment in ("bodyweight", "body weight") or is_bodyweight(exercise.get("name", ""))
    name = exercise.get("name", "")
    if not has_weight and not bodyweight:
        return "weight"
    # Carries/sleds/yoke are measured by distance, not reps.
    if not has_reps and not is_distance_based(name):
        return "reps"
    # A stated "1x5" / "1 set" is a deliberate single set, not a missing detail.
    if len(sets) == 1 and not states_set_count(message):
        return "sets"
    return None


# --- Outcome simulation (AssistantViewModel.send + logWorkout) ----------------

def is_cardio_only(parsed: dict) -> bool:
    if parsed.get("exercises"):
        return False
    return bool(
        parsed.get("cardio_activity")
        or parsed.get("cardio_notes")
        or parsed.get("cardio_distance") is not None
        or parsed.get("duration_minutes") is not None
    )


def simulate_outcome(message: str, parsed: dict | None, parse_error: bool = False) -> dict:
    """Reproduce what the app does with a fresh message.

    Returns a dict with:
      - route:   "question" | "workout"  (the first branch in `send`)
      - final:   "answer" | "register" | "register_cardio" | "clarify:<field>" | "couldnt_parse"
      - missing_field: the field the app would ask about first (if any)

    `final == "register"` means a workout draft is shown immediately (logged on
    the go, no extra tap). `clarify:*` means it's on the logging path but needs a
    follow-up tap/answer.
    """
    # A message carrying loggable content (sets/reps/weight, or a cardio entry)
    # is a log even when phrased as a request/question ("can you record bench
    # 3x10 at 135?", "can you log ran 2 miles"). Only route to chat when it reads
    # like a question AND has no loggable content.
    loggable = looks_like_workout(message) or looks_like_cardio(message)
    # A history question ("what's my one rep max on bench") is answered even
    # when it carries an exercise + number; otherwise a message that reads like
    # a question only routes to chat when it has no loggable content.
    is_question = looks_like_history_question(message) or (
        looks_like_question(message) and not loggable
    )
    route = "question" if is_question else "workout"

    if route == "question":
        # send() -> answer() : goes straight to the chat endpoint, never logs.
        return {"route": route, "final": "answer", "missing_field": None}

    # send() -> logWorkout(allowQuestionFallback=True, allowClarify=True)
    exercises = (parsed or {}).get("exercises") or []
    if parse_error:
        # 422 path: if it looks like a workout missing only a name, ask; else answer.
        if looks_like_workout(message):
            return {"route": route, "final": "clarify:exercise", "missing_field": "exercise"}
        return {"route": route, "final": "answer", "missing_field": None}

    if not exercises:
        if is_cardio_only(parsed or {}):
            return {"route": route, "final": "register_cardio", "missing_field": None}
        if looks_like_workout(message):
            return {"route": route, "final": "clarify:exercise", "missing_field": "exercise"}
        return {"route": route, "final": "answer", "missing_field": None}

    mf = first_missing_field(exercises[0], message)
    if mf is None:
        return {"route": route, "final": "register", "missing_field": None}
    return {"route": route, "final": f"clarify:{mf}", "missing_field": mf}
