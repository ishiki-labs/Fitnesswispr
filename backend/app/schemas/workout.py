import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, field_validator


class ExerciseSetSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    set_id: uuid.UUID | None = None
    set_number: int
    reps: int | None = None
    weight: float | None = None
    weight_unit: str = "lbs"
    duration_seconds: int | None = None

    @field_validator("weight_unit", mode="before")
    @classmethod
    def _default_weight_unit(cls, v):
        # The LLM sends weight_unit: null for bodyweight moves (no weight). The
        # field is a required string, so null would 500; coerce it to "lbs".
        if v is None or (isinstance(v, str) and not v.strip()):
            return "lbs"
        return v


class ExerciseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    exercise_id: uuid.UUID | None = None
    exercise_order: int = 0
    name: str
    equipment: str | None = None
    muscle_group: str | None = None
    notes: str | None = None
    sets: list[ExerciseSetSchema] = []


class WorkoutSessionSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: uuid.UUID | None = None
    device_uuid: uuid.UUID | None = None
    workout_date: date | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    source: str = "voice"
    raw_transcript: str | None = None
    body_weight_lbs: float | None = None
    workout_type: str | None = None
    cardio_notes: str | None = None
    cardio_activity: str | None = None
    cardio_distance: float | None = None
    cardio_distance_unit: str | None = None
    session_notes: str | None = None
    duration_minutes: int | None = None
    exercises: list[ExerciseSchema] = []
