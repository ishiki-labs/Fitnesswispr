from fastapi import APIRouter, Depends

from app.config import settings
from app.ratelimit import RateLimiter, make_rate_limit_dependency
from app.schemas.requests import ParseRequest
from app.schemas.responses import ParseResponse
from app.services import gemini_service

router = APIRouter()

# Shared limiter for the LLM-backed parse endpoint. Exposed at module level so
# it can be tuned/reset in tests.
parse_limiter = RateLimiter(
    max_requests=settings.PARSE_RATE_LIMIT,
    window_seconds=settings.PARSE_RATE_WINDOW_SECONDS,
)
enforce_parse_rate_limit = make_rate_limit_dependency(parse_limiter)


@router.post(
    "/parse",
    response_model=ParseResponse,
    dependencies=[Depends(enforce_parse_rate_limit)],
)
async def parse_workout(request: ParseRequest) -> ParseResponse:
    """
    Parse a voice transcript using Gemini without saving to DB.
    Returns a WorkoutSessionSchema-shaped response with session_id=null.
    """
    body_weight_lbs: float | None = request.context.get("body_weight_lbs")
    # The client passes its LOCAL today (YYYY-MM-DD) so relative dates like
    # "yesterday" resolve in the user's timezone, not the server's.
    today: str | None = request.context.get("today")

    parsed = await gemini_service.parse_transcript(
        transcript=request.transcript,
        unit_preference=request.unit_preference,
        body_weight_lbs=body_weight_lbs,
        today=today,
    )

    # Normalise exercises: rename "sets" key inside each exercise if needed
    exercises = parsed.get("exercises", [])
    normalised_exercises = []
    for idx, ex in enumerate(exercises):
        ex_copy = dict(ex)
        ex_copy.setdefault("exercise_order", idx)
        normalised_exercises.append(ex_copy)

    return ParseResponse(
        session_id=None,
        workout_date=parsed.get("workout_date"),
        workout_type=parsed.get("workout_type"),
        body_weight_lbs=parsed.get("body_weight_lbs"),
        cardio_notes=parsed.get("cardio_notes"),
        cardio_activity=parsed.get("cardio_activity"),
        cardio_distance=parsed.get("cardio_distance"),
        cardio_distance_unit=parsed.get("cardio_distance_unit"),
        session_notes=parsed.get("session_notes"),
        duration_minutes=parsed.get("duration_minutes"),
        exercises=normalised_exercises,
    )
