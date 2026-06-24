import asyncio
import json
import logging
from datetime import date, datetime

from fastapi import HTTPException
from google import genai
from google.genai import types

# Disable Gemini 2.5 "thinking" for extraction tasks — it adds large latency on
# big structured outputs (e.g. a full spreadsheet) with little accuracy gain.
_NO_THINKING = types.ThinkingConfig(thinking_budget=0)

from app.config import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a gym workout parser. Your job is to parse a voice transcript of a workout and return ONLY valid JSON with no markdown fences, no extra commentary.

Context: The user's last known body weight is {body_weight_lbs} lbs. If body weight is not mentioned in this transcript, carry forward the last known value. If the user states a new body weight, use that new value.

Context: Today is {today} ({weekday}). Use this to resolve any spoken date (see rule 11).

Parsing rules:
1. Expand shorthand: "3 sets of 10 at 225" → 3 set objects with set_number 1, 2, 3 each having reps=10, weight=225.
2. A NEW exercise begins whenever a new exercise name appears. Words like "then" / "after that" / "next" are OPTIONAL cues, not required — split on the exercise names themselves. Weight, reps, or sets stated immediately BEFORE or AFTER an exercise name belong to that exercise. Examples:
   - "225 pounds bench press 125 pounds leg press" → two exercises: Bench Press (weight 225) and Leg Press (weight 125).
   - "bench press 225 squat 315" → two exercises.
2a. Exercise names are often garbled by speech-to-text. Map any unclear, misspelled, or phonetically-off name to the CLOSEST standard gym exercise and output the clean canonical name (e.g. "lakh press" → "Leg Press", "incline dumbell" → "Incline Dumbbell Press", "tricep rope" → "Tricep Pushdown", "lat pull" → "Lat Pulldown", "sludge push and pull" → "Sled Push and Pull"). Use the DISTINCTIVE movement words to guide the match — words like "push", "pull", "sled", "carry", "press", "curl", "row", "squat" are strong signals and must be preserved in the result. If you genuinely cannot confidently identify the intended exercise, KEEP the user's spoken words (title-cased) as the name rather than substituting an unrelated movement — e.g. do NOT turn an unclear "sludge push" into "Lunge" or "Clean and Jerk". Never output meaningless gibberish.
3. "bodyweight is 180" or "I weigh 180" → set body_weight_lbs on the session object, NOT on the exercise.
4. Cardio (running, sprints, treadmill, cycling, rowing, HIIT, walking, swimming, etc.): set workout_type "Cardio" for a cardio-only session and fill the session-level cardio fields:
   - cardio_activity: the activity name, capitalized (e.g. "Running", "Sprints", "Cycling", "Rowing", "HIIT", "Treadmill", "Walking", "Swimming").
   - duration_minutes: total minutes if stated ("ran for 25 minutes" → 25, "30 minutes cardio" → 30).
   - cardio_distance + cardio_distance_unit: distance if stated ("3 miles" → 3.0 with unit "mi"; "5k" → 5.0 with unit "km"; "800 meters" → 800 with unit "m").
   - cardio_notes: any leftover detail (e.g. "10x100m", "incline 5", "treadmill").
   A pure cardio entry has NO exercises — leave "exercises" empty. If cardio is mentioned alongside lifts, keep the lifts in "exercises" AND fill the cardio fields.
5. When unit is not stated, infer from unit_preference: {unit_preference}.
6. Bodyweight exercises (push-ups, pull-ups, dips, etc.) → weight=null.
7. Duration exercises (planks, holds) → duration_seconds set, reps=null, weight=null.
8. Classify the WHOLE session as workout_type using these rules:
   - bench press + overhead press + triceps exercises → "Push"
   - pull-ups + rows + curls + lat pulldown → "Pull"
   - squats + deadlifts + RDL + leg press + lunges → "Legs"
   - upper body push AND pull mixed → "Upper"
   - lower body only → "Lower"
   - push + pull + legs all mixed → "Full Body"
   - only cardio → "Cardio"
   - anything else → "Other"
9. The transcript is OFTEN phrased as a request, command, or question wrapped
   around the workout — "can you log bench 3x10 at 135", "will you please record
   squat 5x5", "you logging deadlift 3x5 or what", "log this for me: ...",
   "hey can you record ...". IGNORE the conversational wrapper and extract the
   exercises/sets/reps/weights (or cardio) inside it. A polite or question-style
   phrasing is NOT a reason to refuse — these are workouts to log.
10. ONLY return {{"parse_error": true, "reason": "explanation here"}} when there
    is genuinely NO loggable workout content at all (no exercises AND no cardio),
    e.g. a pure question like "what's my PR on bench?" or "how did I do last
    week?". If ANY exercise/set/rep/weight or cardio activity is present, extract
    it — never return parse_error in that case.
11. DATE — set "workout_date" (YYYY-MM-DD) to the day the workout happened,
    resolved relative to today ({today}, {weekday}):
    - No date mentioned, or "just now"/"today"/"this morning"/"this afternoon"/
      "this evening"/"tonight" → today ({today}). This is the default.
    - "yesterday" / "last night" → the day before today.
    - "the day before yesterday" → today minus 2 days. "N days ago" → today minus
      N days (e.g. "two days ago" → today minus 2). "a week ago"/"last week" →
      today minus 7 days.
    - "last <weekday>" / "this past <weekday>" / "on <weekday>" / "<weekday>" →
      the MOST RECENT date with that weekday strictly before today. Always use the
      closest past one even if it was only 1-2 days ago (e.g. if today is Tuesday,
      "last Monday" = yesterday, not the previous week).
    - An explicit calendar date ("June 10", "6/10", "the 15th") → that date;
      assume the current year unless one is given, and pick the most recent such
      date that is NOT in the future.
    - NEVER return a future date for a logged workout. When unsure, use today.

Return JSON in exactly this format (no markdown, no fences):
{{
  "workout_date": "{today}",
  "workout_type": "Push",
  "body_weight_lbs": null,
  "cardio_notes": null,
  "cardio_activity": null,
  "cardio_distance": null,
  "cardio_distance_unit": null,
  "session_notes": null,
  "duration_minutes": null,
  "exercises": [
    {{
      "name": "Bench Press",
      "equipment": "barbell",
      "muscle_group": "chest",
      "notes": null,
      "sets": [
        {{"set_number": 1, "reps": 12, "weight": 225.0, "weight_unit": "lbs", "duration_seconds": null}}
      ]
    }}
  ]
}}
"""


def _resolve_today(today: str | None) -> tuple[str, str]:
    """Return (YYYY-MM-DD, weekday name) for the reference 'today'.

    Prefers the caller-supplied date (the user's LOCAL today, so relative dates
    like 'yesterday' resolve in their timezone). Falls back to the server date.
    """
    today_str = (today or "").strip()
    try:
        d = datetime.strptime(today_str, "%Y-%m-%d").date()
    except ValueError:
        d = date.today()
    return d.isoformat(), d.strftime("%A")


async def parse_transcript(
    transcript: str,
    unit_preference: str,
    body_weight_lbs: float | None,
    today: str | None = None,
) -> dict:
    """Call Gemini 2.0 Flash to parse a workout transcript into structured JSON."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)

    bw_display = str(body_weight_lbs) if body_weight_lbs is not None else "unknown"
    today_str, weekday = _resolve_today(today)
    system_prompt = SYSTEM_PROMPT.format(
        body_weight_lbs=bw_display,
        unit_preference=unit_preference,
        today=today_str,
        weekday=weekday,
    )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=transcript,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
    except Exception as exc:
        logger.error("Gemini API error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

    raw = response.text
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Failed to decode Gemini JSON response: %s", raw)
        raise HTTPException(status_code=502, detail="Gemini returned invalid JSON") from exc

    if parsed.get("parse_error"):
        raise HTTPException(
            status_code=422,
            detail=parsed.get("reason", "Could not parse workout transcript"),
        )

    return parsed


ASSISTANT_SYSTEM_PROMPT = """You are SpotRep, a friendly, concise gym training assistant.

Answer the user's question using ONLY the workout history provided below. The \
history is the user's logged sessions (most recent first). When asked about a \
personal record (PR), the heaviest weight, "last time", or progress for an \
exercise, compute it from the data. Be specific with numbers and dates.

Rules:
- Keep answers short and conversational (1-3 sentences). No markdown headers.
- Use the user's units as they appear in the data (lbs/kg).
- If the history doesn't contain the answer, say so briefly and suggest logging it.
- Today's date is {today}.
- Do not invent workouts that aren't in the history.
- You CANNOT log, save, record, edit, or delete workouts — you only answer
  questions about the history above. NEVER claim to have logged, saved, recorded,
  or added anything. If the user is trying to log or correct a workout, tell them
  to state it as a workout to log (e.g. "sled push and pull 3x1 at 100").

WORKOUT HISTORY:
{history}
"""


IMPORT_SHEET_PROMPT = """You are a fitness data extractor. You are given the raw cell grid of ONE person's workout-log spreadsheet tab. The layout is a hypertrophy "mesocycle": blocks labeled like "MESO #1 / DAY 1", each block lists exercises down the rows, and columns are grouped by WEEK (Week 1..8), each week having three sub-columns: SETS, REPS, WEIGHT.

Extract every cell that contains ACTUAL logged data into structured JSON.

CRITICAL DATA-REPAIR RULES:
1. Excel auto-converted many rep/weight values into dates. Any value that looks like an ISO datetime (e.g. "2012-12-12 00:00:00", "2010-12-10 00:00:00", "2008-08-08 00:00:00") is NOT a date — it is a slash-separated triple of numbers. Convert using month/day/last-two-of-year in order: "2012-12-12 00:00:00" -> 12/12/12, "2010-12-10 00:00:00" -> 10/12/10, "2008-08-08 00:00:00" -> 8/8/8.
2. REPS like "9 / 12 / 12" means three sets with reps 9, 12, 12. WEIGHT like "45 / 50 /" means sets at 45, 50, and a third unknown. Pair reps and weights by position across the sets for that week. If counts differ, pair what you can and leave missing values null.
3. Values like "8 - 12" or "8-12" are TARGET ranges, not actual logged sets — SKIP exercises/weeks that only have a target and no actual numbers.
4. "lvl 8", "Floor", "Full push-up", "Step ups", "A", etc. are notes — put them in the exercise "notes", never as a weight.
5. "1 minute" / holds -> duration_seconds (e.g. 60), reps null, weight null.
6. Map garbled or abbreviated exercise names to the closest standard gym exercise name.
7. Determine the unit (lbs unless clearly kg).

For each (week, day) that has at least one exercise with real data, output one workout. Classify workout_type from the exercises (Push/Pull/Legs/Upper/Lower/Full Body/Cardio/Other).

Return ONLY JSON (no markdown fences) in exactly this shape:
{
  "unit": "lbs",
  "workouts": [
    {"week": 1, "day": 1, "day_label": "Day 1", "workout_type": "Push",
     "exercises": [
       {"name": "Bench Press", "muscle_group": "chest", "notes": null,
        "sets": [{"reps": 12, "weight": 135.0, "weight_unit": "lbs", "duration_seconds": null}]}
     ]}
  ]
}
If nothing usable is present, return {"unit": "lbs", "workouts": []}."""


IMPORT_PHOTO_PROMPT = """You extract workout data from a PHOTO of a person's workout record (handwritten notebook, printed sheet, app screenshot, or whiteboard).

Read every exercise with its sets, reps, and weights. Rules:
- "3x10 @ 135" or "3 sets of 10 at 135" -> three sets of reps 10, weight 135.
- Per-set values "10/8/6" -> three sets with those reps.
- Bodyweight moves -> weight null. Holds/planks -> duration_seconds, reps/weight null.
- Map abbreviated/garbled names to the closest standard gym exercise.
- Determine the unit (lbs unless clearly kg).
- If a date is visible, return it as workout_date in YYYY-MM-DD; otherwise null.

Return ONLY JSON (no fences):
{
  "unit": "lbs",
  "workouts": [
    {"workout_date": null, "workout_type": "Push",
     "exercises": [
       {"name": "Bench Press", "muscle_group": "chest", "notes": null,
        "sets": [{"reps": 10, "weight": 135.0, "weight_unit": "lbs", "duration_seconds": null}]}
     ]}
  ]
}
If nothing usable, return {"unit": "lbs", "workouts": []}."""


def _strip_json(raw: str) -> dict:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1] if "```" in text else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    return json.loads(text)


async def extract_spreadsheet_sheet(grid_text: str) -> dict:
    """Extract one person's logged workouts from a spreadsheet tab grid."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=grid_text,
            config=types.GenerateContentConfig(
                system_instruction=IMPORT_SHEET_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
                thinking_config=_NO_THINKING,
            ),
        )
    except Exception as exc:
        logger.error("Gemini API error (import sheet): %s", exc)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    try:
        return _strip_json(response.text)
    except json.JSONDecodeError as exc:
        logger.error("Bad import-sheet JSON: %s", response.text[:500])
        raise HTTPException(status_code=502, detail="Could not read the spreadsheet") from exc


async def extract_photo(image_bytes: bytes, mime: str) -> dict:
    """Extract workouts from a photo of a workout record."""
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=mime),
                "Extract the workouts from this image.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=IMPORT_PHOTO_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
                thinking_config=_NO_THINKING,
            ),
        )
    except Exception as exc:
        logger.error("Gemini API error (import photo): %s", exc)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc
    try:
        return _strip_json(response.text)
    except json.JSONDecodeError as exc:
        logger.error("Bad import-photo JSON: %s", response.text[:500])
        raise HTTPException(status_code=502, detail="Could not read the photo") from exc


async def answer_question(
    question: str,
    history: str,
    today: str,
    conversation: list[dict] | None = None,
) -> str:
    """Answer a free-form question grounded in the user's workout history.

    `conversation` is the recent chat transcript (prior turns, oldest first) so
    follow-up questions ("what about last week?", "and on bench?") keep context.
    Each turn is {"role": "user"|"assistant", "content": str}; the current
    `question` is appended as the final user turn.
    """
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    system_prompt = ASSISTANT_SYSTEM_PROMPT.format(
        today=today,
        history=history or "(no workouts logged yet)",
    )

    contents: list[dict] = []
    for turn in conversation or []:
        text = (turn.get("content") or "").strip()
        if not text:
            continue
        role = "user" if turn.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": text}]})
    contents.append({"role": "user", "parts": [{"text": question}]})

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=0.3,
            ),
        )
    except Exception as exc:
        logger.error("Gemini API error (assistant): %s", exc)
        raise HTTPException(status_code=502, detail=f"Gemini API error: {exc}") from exc

    return (response.text or "").strip()
