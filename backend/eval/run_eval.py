"""Run a chatbot eval dataset against the real parser + client routing.

For every sample we:
  1. Call the SAME parser the /parse endpoint uses (in-process, so no HTTP rate
     limit) on the raw message.
  2. Reproduce the iOS client's routing to decide what the app would actually do
     (log it / ask a follow-up / answer as a question).
  3. Score the outcome against the sample's `expect`.

This separates the two failure modes the user is seeing:
  - ROUTING failures: the parser *could* extract the workout, but the app sent
    the message to the chat endpoint (or asked an unnecessary follow-up) so it
    never got logged. (e.g. "can you record bench 3x10 at 135")
  - PARSE failures: the message was routed to logging but the parser produced the
    wrong exercise/sets/reps/weight.

Run:
    cd backend
    .venv/bin/python -m eval.run_eval                       # category 1, 100 samples
    .venv/bin/python -m eval.run_eval --limit 20            # quick smoke
    .venv/bin/python -m eval.run_eval --dataset eval/datasets/01_register_phrasings.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import time

from app.schemas.responses import ParseResponse
from app.services import gemini_service
from eval.routing import looks_like_question, looks_like_workout, simulate_outcome


def response_validation_error(parsed: dict) -> str | None:
    """Build the ParseResponse exactly like the /parse endpoint does.

    If it raises, the real endpoint would return an unhandled HTTP 500 (this is
    how bodyweight `weight_unit: null` used to crash the app). Returns the error
    string on failure, or None when the response is well-formed.
    """
    try:
        exercises = []
        for idx, ex in enumerate(parsed.get("exercises", []) or []):
            ex_copy = dict(ex)
            ex_copy.setdefault("exercise_order", idx)
            exercises.append(ex_copy)
        ParseResponse(
            session_id=None,
            workout_type=parsed.get("workout_type"),
            body_weight_lbs=parsed.get("body_weight_lbs"),
            cardio_notes=parsed.get("cardio_notes"),
            cardio_activity=parsed.get("cardio_activity"),
            cardio_distance=parsed.get("cardio_distance"),
            cardio_distance_unit=parsed.get("cardio_distance_unit"),
            session_notes=parsed.get("session_notes"),
            duration_minutes=parsed.get("duration_minutes"),
            exercises=exercises,
        )
        return None
    except Exception as exc:  # pydantic ValidationError -> 500 in prod
        return str(exc).splitlines()[0]

DEFAULT_DATASET = "eval/datasets/01_register_phrasings.jsonl"
RESULTS_DIR = pathlib.Path(__file__).parent / "results"
CACHE_PATH = RESULTS_DIR / ".parse_cache.json"

# Parser output is independent of the (client-side) routing/scoring logic, so we
# cache it by message. This lets you iterate on routing, scoring, and the dataset
# without re-hitting Gemini. Pass --refresh after changing the parser/prompt.
_parse_cache: dict[str, dict] = {}
_cache_lock = asyncio.Lock()


def load_cache() -> None:
    global _parse_cache
    if CACHE_PATH.exists():
        try:
            _parse_cache = json.loads(CACHE_PATH.read_text())
        except Exception:
            _parse_cache = {}


def save_cache() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(_parse_cache))


# --- parsing with retry/backoff (Gemini occasionally 429s/502s) ---------------

async def parse_with_retry(
    message: str, sem: asyncio.Semaphore, use_cache: bool = True, retries: int = 4,
    today: str | None = None,
) -> dict:
    # Cache by (today, message): the same message resolves to a different date
    # depending on the reference "today", so date-aware samples can't share a key
    # with the plain ones. today=None keeps the original message-only key.
    key = message if today is None else f"{today}\x00{message}"
    if use_cache and key in _parse_cache:
        return _parse_cache[key]
    delay = 1.5
    async with sem:
        if use_cache and key in _parse_cache:  # filled while we waited
            return _parse_cache[key]
        result = {"parsed": {"exercises": []}, "parse_error": False, "error": "unreachable"}
        for attempt in range(retries):
            try:
                parsed = await gemini_service.parse_transcript(message, "lbs", None, today=today)
                result = {"parsed": parsed, "parse_error": False, "error": None}
                break
            except Exception as exc:  # HTTPException(422)=parse_error, others=transient
                status = getattr(exc, "status_code", None)
                if status == 422:
                    result = {"parsed": {"exercises": []}, "parse_error": True, "error": str(exc)}
                    break
                if attempt == retries - 1:
                    result = {"parsed": {"exercises": []}, "parse_error": False, "error": f"transient: {exc}"}
                    break
                await asyncio.sleep(delay)
                delay *= 2
    async with _cache_lock:
        _parse_cache[key] = result
    return result


# --- scoring ------------------------------------------------------------------

def _tokens(name: str) -> set[str]:
    filler = {"the", "a", "with", "and", "of", "on", "machine", "barbell",
              "dumbbell", "cable", "smith"}
    out = set()
    for tok in "".join(c if c.isalpha() else " " for c in name.lower()).split():
        # Crude singularisation for matching only (symmetric on both names), so
        # "Pull Ups" ({pull, up}) matches "pull up". Handle sibilant "-es"
        # plurals ("crunches" -> "crunch") and "-ies" ("flies" -> "fly") before
        # the bare "-s" rule. Threshold >2 so short plurals like "ups" -> "up".
        if len(tok) > 3 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 4 and tok.endswith(("ches", "shes", "sses", "xes", "zes")):
            tok = tok[:-2]
        elif len(tok) > 2 and tok.endswith("s"):
            tok = tok[:-1]
        if tok and tok not in filler:
            out.add(tok)
    return out


def name_matches(expected: str, actual: str) -> bool:
    a, b = _tokens(expected), _tokens(actual)
    if not a:
        return True
    # All of the expected movement tokens must be present in the parsed name.
    # (A loose overlap rule wrongly matched e.g. "incline dumbbell press" to
    # "Bench Press" because both contain "press".)
    return a.issubset(b)


def find_exercise(parsed: dict, expected_name: str) -> dict | None:
    for ex in parsed.get("exercises") or []:
        if name_matches(expected_name, ex.get("name", "")):
            return ex
    return None


def _check_one(ex: dict, spec: dict) -> list[str]:
    problems: list[str] = []
    sets = ex.get("sets") or []
    if spec.get("sets") is not None and len(sets) != spec["sets"]:
        problems.append(f"{spec.get('exercise')}: sets={len(sets)} want {spec['sets']}")
    reps_vals = {s.get("reps") for s in sets}
    if spec.get("reps") is not None and spec["reps"] not in reps_vals:
        problems.append(f"{spec.get('exercise')}: reps={sorted(r for r in reps_vals if r is not None)} want {spec['reps']}")
    weights = {float(s["weight"]) for s in sets if s.get("weight") is not None}
    if spec.get("weight") is not None and not any(abs(w - spec["weight"]) < 0.5 for w in weights):
        problems.append(f"{spec.get('exercise')}: weight={sorted(weights)} want {spec['weight']}")
    return problems


def expected_specs(expect: dict) -> list[dict]:
    """Normalise expect into a list of per-exercise specs."""
    if expect.get("exercises"):
        return expect["exercises"]
    if expect.get("exercise"):
        return [{k: expect.get(k) for k in ("exercise", "sets", "reps", "weight", "forbid")}]
    return []


def check_register(parsed: dict, expect: dict) -> tuple[bool, list[str]]:
    """Did the parser extract the right exercise(s)/sets/reps/weight?"""
    problems: list[str] = []
    for spec in expected_specs(expect):
        ex = find_exercise(parsed, spec["exercise"])
        if ex is None:
            names = [e.get("name") for e in parsed.get("exercises") or []]
            problems.append(f"{spec['exercise']!r} not found (got {names})")
            continue
        problems += _check_one(ex, spec)
        # The parsed name must not carry a wrong/negated movement (e.g. a
        # correction "sled push not leg press" must not log "...not leg press"
        # or "Lunge"). `forbid` lists tokens that must be absent.
        forbid = spec.get("forbid") or []
        if forbid:
            atoks = _tokens(ex.get("name", ""))
            bad = [f for f in forbid if f in atoks]
            if bad:
                problems.append(f"{spec['exercise']}: name {ex.get('name')!r} contains forbidden {bad}")
    return (len(problems) == 0), problems


# Treat synonyms as the same activity (jog == run, cycle == bike, etc.).
_CARDIO_SYN = {
    "run": "run", "running": "run", "ran": "run", "jog": "run", "jogging": "run", "jogged": "run",
    "sprint": "sprint", "sprints": "sprint",
    "cycle": "bike", "cycling": "bike", "bike": "bike", "biking": "bike", "biked": "bike",
    "row": "row", "rowing": "row", "rowed": "row",
    "swim": "swim", "swimming": "swim", "swam": "swim",
    "walk": "walk", "walking": "walk", "walked": "walk",
    "treadmill": "treadmill", "hiit": "hiit", "elliptical": "elliptical",
}


def _norm_activity(s: str) -> set[str]:
    return {_CARDIO_SYN.get(t, t) for t in _tokens(s)}


def check_date(parsed: dict, sample: dict) -> tuple[bool, str | None]:
    """Did the workout land on the right day?

    The reported bug: "I did bench yesterday" saves TODAY. We model the date the
    app would actually persist: the iOS client ignores any parser date (its
    ParsedSession has no date field) and stamps the DatePicker, which defaults to
    Date() == today. So the effective saved date is the parser's `workout_date`
    when present, else `today`. Only samples whose `expect` carries a
    `workout_date` are checked; everything else is unaffected.
    """
    want_date = (sample.get("expect") or {}).get("workout_date")
    if not want_date:
        return True, None
    today = sample.get("today")
    effective = parsed.get("workout_date") or today
    if effective == want_date:
        return True, None
    return False, f"date={effective} want {want_date} (parsed_date={parsed.get('workout_date')})"


def check_cardio(parsed: dict, expect: dict) -> tuple[bool, list[str]]:
    act = (parsed.get("cardio_activity") or "").strip()
    if not act:
        return False, ["no cardio_activity parsed"]
    want = expect.get("activity")
    if want and not (_norm_activity(want) & _norm_activity(act)):
        return False, [f"activity={act!r} want {want!r}"]
    return True, []


# --- per-sample evaluation ----------------------------------------------------

async def eval_sample(sample: dict, sem: asyncio.Semaphore, use_cache: bool) -> dict:
    message = sample["message"]
    expect = sample["expect"]
    want = expect["action"]  # register | clarify | answer | register_cardio
    res = await parse_with_retry(message, sem, use_cache=use_cache, today=sample.get("today"))
    parsed, parse_error = res["parsed"], res["parse_error"]

    outcome = simulate_outcome(message, parsed, parse_error)
    final = outcome["final"]

    passed = False
    content_ok = True
    problems: list[str] = []
    fail_kind = None          # routing | parse | server_500 | other
    fail_reason = None

    # The /parse endpoint would build a ParseResponse from this dict. If that
    # validation raises, the real app gets an HTTP 500 ("Something went wrong:
    # Server error 500") and nothing is logged — count it as a hard failure.
    server_500 = None if parse_error else response_validation_error(parsed)
    if server_500 is not None and want != "answer":
        return {
            "id": sample["id"], "category": sample.get("category"), "message": message,
            "want": want, "route": outcome["route"], "final": "server_500",
            "passed": False, "content_ok": False,
            "fail_kind": "server_500", "fail_reason": f"500 ({server_500})",
            "cardio_activity": parsed.get("cardio_activity"),
            "parsed_exercises": [], "error": res["error"],
        }

    if want == "register":
        content_ok, problems = check_register(parsed, expect)
        date_ok, date_problem = check_date(parsed, sample)
        if not date_ok:
            problems = problems + [date_problem]
        parse_ok = bool(parsed.get("exercises")) and content_ok
        passed = (final == "register") and content_ok and date_ok
        if not passed:
            if parse_ok and content_ok and date_ok and final != "register":
                fail_kind, fail_reason = "routing", f"routing ({final})"
            elif not parsed.get("exercises"):
                fail_kind, fail_reason = "parse", f"empty_parse ({final})"
            elif content_ok and not date_ok:
                # Exercise extracted fine; only the date is wrong.
                fail_kind, fail_reason = "date", date_problem
            else:
                fail_kind, fail_reason = "parse", f"parse ({'; '.join(problems)})"

    elif want == "clarify":
        field = expect.get("field")
        target = f"clarify:{field}" if field else "clarify"
        content_ok = (not expect.get("exercise")) or (find_exercise(parsed, expect["exercise"]) is not None)
        passed = (final == target) and content_ok
        if not passed:
            if final == "answer":
                fail_kind, fail_reason = "routing", "routing (answer)"
            elif final == "register":
                fail_kind, fail_reason = "parse", "no_followup (logged without asking)"
            elif final.startswith("clarify"):
                fail_kind, fail_reason = "parse", f"wrong_followup ({final} want {target})"
            else:
                fail_kind, fail_reason = "other", f"got {final} want {target}"

    elif want == "answer":
        passed = (final == "answer")
        if not passed:
            fail_kind, fail_reason = "routing", f"routing ({final})"  # a question got logged/clarified

    elif want == "register_cardio":
        content_ok, problems = check_cardio(parsed, expect)
        passed = (final == "register_cardio") and content_ok
        if not passed:
            if final == "answer":
                fail_kind, fail_reason = "routing", "routing (answer)"
            elif final == "register":
                fail_kind, fail_reason = "parse", "parsed as strength, not cardio"
            else:
                fail_kind, fail_reason = "parse", f"cardio ({'; '.join(problems)}) [{final}]"

    return {
        "id": sample["id"],
        "category": sample.get("category"),
        "message": message,
        "want": want,
        "route": outcome["route"],
        "final": final,
        "passed": passed,
        "content_ok": content_ok,
        "fail_kind": fail_kind,
        "fail_reason": fail_reason,
        "cardio_activity": parsed.get("cardio_activity"),
        "parsed_exercises": [
            {"name": e.get("name"), "sets": len(e.get("sets") or []),
             "reps": sorted({s.get("reps") for s in (e.get("sets") or []) if s.get("reps") is not None}),
             "weights": sorted({float(s["weight"]) for s in (e.get("sets") or []) if s.get("weight") is not None})}
            for e in parsed.get("exercises") or []
        ],
        "error": res["error"],
    }


async def run(dataset: pathlib.Path, limit: int | None, concurrency: int, use_cache: bool) -> dict:
    samples = [json.loads(l) for l in dataset.open() if l.strip()]
    if limit:
        samples = samples[:limit]
    sem = asyncio.Semaphore(concurrency)

    start = time.time()
    done = 0
    results: list[dict] = [None] * len(samples)

    async def worker(idx: int, sample: dict):
        nonlocal done
        results[idx] = await eval_sample(sample, sem, use_cache)
        done += 1
        if done % 10 == 0 or done == len(samples):
            print(f"  ...{done}/{len(samples)}", flush=True)

    await asyncio.gather(*(worker(i, s) for i, s in enumerate(samples)))
    elapsed = time.time() - start

    passed = sum(1 for r in results if r["passed"])
    routing_fails = sum(1 for r in results if r["fail_kind"] == "routing")
    parse_fails = sum(1 for r in results if r["fail_kind"] == "parse")
    server_500_fails = sum(1 for r in results if r["fail_kind"] == "server_500")
    date_fails = sum(1 for r in results if r["fail_kind"] == "date")
    other_fails = sum(1 for r in results if r["fail_kind"] == "other")

    return {
        "dataset": str(dataset),
        "total": len(results),
        "passed": passed,
        "pass_rate": round(100 * passed / max(1, len(results)), 1),
        "routing_failures": routing_fails,
        "parse_failures": parse_fails,
        "server_500_failures": server_500_fails,
        "date_failures": date_fails,
        "other_failures": other_fails,
        "elapsed_s": round(elapsed, 1),
        "results": results,
    }


def print_report(summary: dict) -> None:
    print("\n" + "=" * 64)
    print(f"DATASET: {summary['dataset']}")
    print(f"PASS:    {summary['passed']}/{summary['total']}  ({summary['pass_rate']}%)")
    print(f"  routing failures (parser ok, app misrouted): {summary['routing_failures']}")
    print(f"  parse failures   (wrong/empty extraction):   {summary['parse_failures']}")
    print(f"  server 500s      (response validation crash):{summary.get('server_500_failures', 0)}")
    print(f"  date failures    (wrong workout_date / day):  {summary.get('date_failures', 0)}")
    print(f"  other failures:                              {summary['other_failures']}")
    print(f"  time: {summary['elapsed_s']}s")
    print("=" * 64)

    fails = [r for r in summary["results"] if not r["passed"]]
    if fails:
        print(f"\nFAILURES ({len(fails)}):")
        for r in fails:
            print(f"  [{r['fail_reason']:>22}] {r['message']!r}")


async def amain() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--refresh", action="store_true", help="ignore parse cache (re-hit Gemini)")
    ap.add_argument("--out", default=None, help="path to write detailed results jsonl")
    args = ap.parse_args()

    use_cache = not args.refresh
    if use_cache:
        load_cache()

    dataset = pathlib.Path(args.dataset)
    print(f"Running eval: {dataset} (limit={args.limit}, concurrency={args.concurrency}, "
          f"cache={'on' if use_cache else 'off'})")
    summary = await run(dataset, args.limit, args.concurrency, use_cache)
    save_cache()
    print_report(summary)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = pathlib.Path(args.out) if args.out else RESULTS_DIR / (dataset.stem + ".result.json")
    with out.open("w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(amain())
