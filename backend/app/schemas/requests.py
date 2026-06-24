import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class ExerciseSetCreate(BaseModel):
    set_number: int
    reps: int | None = None
    weight: Decimal | None = None
    weight_unit: str = "lbs"
    duration_seconds: int | None = None

    @field_validator("weight_unit", mode="before")
    @classmethod
    def _default_weight_unit(cls, v):
        # Bodyweight sets arrive with weight_unit: null; coerce to avoid a 500.
        if v is None or (isinstance(v, str) and not v.strip()):
            return "lbs"
        return v


class ExerciseCreate(BaseModel):
    exercise_order: int = 0
    name: str
    equipment: str | None = None
    muscle_group: str | None = None
    notes: str | None = None
    sets: list[ExerciseSetCreate] = []


class ParseRequest(BaseModel):
    transcript: str
    device_uuid: str
    unit_preference: str = "lbs"
    context: dict = Field(default_factory=dict)


class ProfileUpdateRequest(BaseModel):
    name: str | None = None


class GrantCreateRequest(BaseModel):
    grantee_uuid: str
    access: str = "read"
    grantee_name: str | None = None


class HealthWorkoutItem(BaseModel):
    workout_date: date
    category: str
    symbol: str
    duration_minutes: int = 0


class HealthSyncRequest(BaseModel):
    device_uuid: str
    workouts: list[HealthWorkoutItem] = []


class AppleAuthRequest(BaseModel):
    # The identity token returned by Sign in with Apple on the device.
    identity_token: str
    # The device's current local (anonymous) UUID, so its data can be merged
    # into the account on first sign-in from a new device.
    device_uuid: str | None = None
    # Apple only returns the user's name on the very first authorization.
    full_name: str | None = None


class CreateSessionRequest(BaseModel):
    device_uuid: uuid.UUID | None = None
    workout_date: date
    source: str = "voice"
    raw_transcript: str | None = None
    body_weight_lbs: Decimal | None = None
    workout_type: str | None = None
    cardio_notes: str | None = None
    # Structured cardio (e.g. a run/sprint logged on its own). Kept separate
    # from the muscle map / strength PRs — a cardio entry has no exercises.
    cardio_activity: str | None = None
    cardio_distance: Decimal | None = None
    cardio_distance_unit: str | None = None
    session_notes: str | None = None
    duration_minutes: int | None = None
    exercises: list[ExerciseCreate] = []


class UpdateSessionRequest(BaseModel):
    workout_date: date | None = None
    source: str | None = None
    raw_transcript: str | None = None
    body_weight_lbs: Decimal | None = None
    workout_type: str | None = None
    cardio_notes: str | None = None
    cardio_activity: str | None = None
    cardio_distance: Decimal | None = None
    cardio_distance_unit: str | None = None
    session_notes: str | None = None
    duration_minutes: int | None = None
    exercises: list[ExerciseCreate] | None = None
