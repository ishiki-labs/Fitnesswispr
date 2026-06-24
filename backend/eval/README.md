# Chatbot eval harness

Benchmarks the SpotRep coach end-to-end: does a chat message get **logged**,
**clarified**, or **answered** the way we expect? It exercises the *whole*
pipeline, not just the parser — it reproduces the iOS client's routing
(`looksLikeQuestion` / `looksLikeWorkout`) and then calls the same Gemini parser
the `/parse` endpoint uses (in-process, so no HTTP rate limit).

This matters because most current bugs are **routing**, not parsing: e.g.
"can you record bench 3x10 at 135?" parses perfectly, but the old client sent it
to the chat endpoint and never logged it.

## Run

```bash
cd backend
.venv/bin/python -m eval.run_eval                 # category 1, 100 samples (cached)
.venv/bin/python -m eval.run_eval --limit 20      # quick subset
.venv/bin/python -m eval.run_eval --refresh       # ignore cache, re-hit Gemini
```

Parser outputs are cached by message in `results/.parse_cache.json`, so you can
iterate on routing/scoring/dataset instantly. Use `--refresh` after changing the
parser prompt.

## Layout

- `routing.py` — Python port of the client's routing. **Keep in lock-step with
  `AssistantViewModel.swift`.** (Long term: move routing server-side so both the
  app and this harness call one source of truth.)
- `generate.py` — builds datasets into `datasets/*.jsonl`.
- `run_eval.py` — runs a dataset, scores it, splits failures into **routing**,
  **parse**, and **server_500** (the parsed dict is fed through the real
  `ParseResponse` model, so a validation crash is caught here instead of in the
  app), writes `results/<dataset>.result.json`.
- `datasets/` — the benchmark samples (one JSON object per line).

## Sample format

```json
{"id": "register_phrasings-001", "category": "register_phrasings",
 "message": "can you please record bench 3x10 at 135",
 "expect": {"action": "register", "exercise": "bench press",
            "sets": 3, "reps": 10, "weight": 135.0, "unit": "lbs"}}
```

`expect.action` is one of `register` (log it on the go), `clarify` (ask a
follow-up first), or `answer` (route to the Q&A chat).

## Categories & current scores

Each is 100 samples. Scores are after the routing fixes (`looksLikeWorkout` /
`looksLikeCardio` gate) and tightened scoring.

| # | dataset | what it checks | before | after |
|---|---------|----------------|--------|-------|
| 1 | `01_register_phrasings` | ways of asking to log a full set (polite/terse/question-form) | 88 | **100** |
| 2 | `02_missing_weight` | missing weight → asks a follow-up | 99 | **99** |
| 3 | `03_missing_reps_sets` | missing reps or sets → asks a follow-up | 100 | **100** |
| 4 | `04_fully_specified` | varied/multi-exercise/kg → extracts correctly | 96* | **100** |
| 5 | `05_rude_scale` | rude/casual/typo'd phrasings at scale | 100 | **100** |
| 6 | `06_qa_lasttime` | "what did I do / my PR" → answered, NOT logged | 100 | **100** |
| 7 | `07_cardio` | "ran 3 miles", "sprints 10x100m" → cardio entry | 86 | **100** |
| 8 | `08_tough_failures` | TestFlight breakages: bodyweight/holds/voice numbers | 60 | **100** |
| 9 | `09_garbled_corrections` | ASR-garbled novel names + spoken corrections ("X not Y") | — | **~97** |
| 10 | `10_sc_catalog` | every common strength & conditioning movement logs first try | 127/142 | **140/142** |
| 11 | `11_relative_dates` | spoken dates ("yesterday", "last friday", "june 10th") land on the right day | 6/38 | **41/42** |

\* category 4's pre-fix misses were a scoring bug (loose name matching), not the parser.
Categories 9–11 are hand-authored and smaller than 100 samples (34 / 142 / 42).

## Category 8: the tough TestFlight cases

Built from real failures a tester hit in build 1.0 (24). Each failure mode now
has a fix on both sides (server + client routing port):

- **Server 500 on bodyweight moves.** "record 3x12 box jumps with body weight"
  → the LLM returns `weight_unit: null`, which the `weight_unit: str` schema
  rejected → unhandled `ValidationError` → HTTP 500 ("Something went wrong:
  Server error 500"). Fixed with a `weight_unit` validator (null → "lbs") in
  both `schemas/workout.py` and `schemas/requests.py`. The harness now builds
  `ParseResponse` so this class of crash is caught as `server_500`.
- **Spelled-out numbers misrouted.** Voice transcribes "three sets twelve reps
  … hundred lb" with no digits, so `looksLikeWorkout` (digit-gated) returned
  false and a "can you record …" request fell through to the Q&A chat. Fixed by
  accepting number *words* (`_NUMBER_WORDS`) in the workout/cardio guards.
- **Bodyweight & timed holds nagged / didn't register.** Expanded the
  bodyweight list (plyometrics, holds), skip the weight prompt when
  `equipment == "bodyweight"`, and added duration signals (`second`, `minute`,
  `hold`) so "45 second plank" routes to logging.
- **History questions.** Added `looksLikeHistoryQuestion` so "what's my one rep
  max on bench" is still answered even though it now trips the (broadened)
  workout signal.

Re-run with `--refresh` to re-validate after a parser/prompt change.

## Category 9: garbled names & spoken corrections

From TestFlight: "sludge push and pull" (= sled push and pull) and corrections
like "X not Y" / "no I meant …". Findings:

- **Parser is robust (~97%).** When the numbers are present it recovers the name
  ("sludge push and pull" → `Sled Push`/`Sled Pull`) and correctly *ignores* the
  negated half of a correction ("sled push and pull not leg press, 3x1 at 100"
  → `Sled Push and Pull`). The `forbid` field in `expect` asserts the parsed
  name never carries the wrong/negated movement (`leg`, `not`, `lunge`, …).
- **Residual parser miss:** badly-degraded names where the keyword is lost
  ("sludge each … one wrap") or mis-mapped ("sledge" → `Sledgehammer`) get
  logged as the nearest common lift (`Lunge`). Non-deterministic.
- **The real breakage is client-side** and is NOT captured here (the harness
  runs with empty history): (1) the history-driven variant clarification
  ("Which sled push?") stores the *typed* answer verbatim as the exercise name
  ("Sludge push and pull not leg press"); (2) a standalone correction 422s and
  the client routes it to chat, which *hallucinates* "Got it! I've logged …"
  while nothing is saved. Fix belongs in `AssistantViewModel` (re-parse
  clarification answers; don't fall a correction back to chat).

## Category 11: relative / spoken dates

Benchmarks the reported bug: **"I did bench yesterday" saves with today's date.**

Each sample logs a fully-specified set with a spoken date phrase and a pinned
reference "today" (`2026-06-23`, a Tuesday) so the expected `workout_date` is
reproducible. The score models the date the app would actually persist: the
parser's `workout_date` if it returns one, **else today** — because the iOS
client's `ParsedSession` has no date field and the save DatePicker defaults to
`Date()`. The new `date` failure bucket (in `run_eval.py`) flags these.

**Baseline (before fix): 6/38 (15.8%).** Every exercise/sets/reps/weight was
extracted perfectly (0 parse/routing failures) — only the **date** was wrong. The
6 passes were exactly the "today" phrasings, which passed only because the bug
coincided with today. All past/explicit-date cases were saved as today.

**After fix: 41/42 (97.6%).** The lone miss is a non-deterministic LLM slip on
one "last sunday" variant (the other passes). All of the user-critical buckets
are 100%: no date → today (default), "today"/"this morning", "yesterday"/"last
night", "day before yesterday"/"N days ago", and explicit dates ("june 10th",
"the 15th").

The fix is two-sided:
1. **Parser** (`gemini_service.SYSTEM_PROMPT`, rule 11) is now told today's date +
   weekday and emits a resolved `workout_date`. The client's LOCAL today is passed
   via `ParseRequest.context.today` so relative dates resolve in the user's
   timezone (verified across a year boundary).
2. **Client** decodes `ParsedSession.workoutDate` and pre-fills the confirmation
   DatePicker with it (still editable); the Siri quick-action saves it directly.

The harness passes each sample's pinned `today` to the parser and caches by
`(today, message)`. Re-run with `--refresh` after a prompt change:

```bash
.venv/bin/python -m eval.run_eval --dataset eval/datasets/11_relative_dates.jsonl --refresh
```
