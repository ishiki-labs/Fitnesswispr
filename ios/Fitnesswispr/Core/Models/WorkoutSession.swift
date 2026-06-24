import Foundation

struct ExerciseSet: Codable, Identifiable, Equatable {
    var id: String { "\(setNumber)" }
    var setNumber: Int
    var reps: Int?
    var weight: Double?
    var weightUnit: String
    var durationSeconds: Int?
}

struct Exercise: Codable, Identifiable, Equatable {
    var id: String = UUID().uuidString
    var exerciseId: String?
    var name: String
    var equipment: String?
    var muscleGroup: String?
    var notes: String?
    var sets: [ExerciseSet]

    enum CodingKeys: String, CodingKey {
        case exerciseId, name, equipment, muscleGroup, notes, sets
    }
}

struct WorkoutSession: Codable, Identifiable {
    var id: String { sessionId ?? tempId }
    private let tempId: String = UUID().uuidString
    var sessionId: String?
    var deviceUuid: String?
    var workoutDate: String
    var createdAt: String?
    var source: String?
    var rawTranscript: String?
    var workoutType: String?
    var bodyWeightLbs: Double?
    var cardioNotes: String?
    var cardioActivity: String?
    var cardioDistance: Double?
    var cardioDistanceUnit: String?
    var sessionNotes: String?
    var durationMinutes: Int?
    var exercises: [Exercise]

    enum CodingKeys: String, CodingKey {
        case sessionId, deviceUuid, workoutDate, createdAt, source, rawTranscript
        case workoutType, bodyWeightLbs, cardioNotes
        case cardioActivity, cardioDistance, cardioDistanceUnit
        case sessionNotes, durationMinutes, exercises
    }

    /// A standalone cardio entry (a run/sprint/etc. logged on its own, with no
    /// strength exercises). These never appear in the muscle map or strength PRs.
    var isCardioOnly: Bool {
        exercises.isEmpty && (cardioActivity?.isEmpty == false || cardioNotes?.isEmpty == false)
    }
}

struct ParsedSession: Codable {
    /// The day the parser resolved from the transcript (e.g. "yesterday",
    /// "last friday"), as YYYY-MM-DD. Nil when the parser didn't return one.
    var workoutDate: String?
    var workoutType: String?
    var bodyWeightLbs: Double?
    var cardioNotes: String?
    var cardioActivity: String?
    var cardioDistance: Double?
    var cardioDistanceUnit: String?
    var durationMinutes: Int?
    var exercises: [Exercise]

    /// True when the parse produced a cardio session with no strength exercises.
    var isCardioOnly: Bool {
        exercises.isEmpty &&
            (cardioActivity?.isEmpty == false
                || cardioNotes?.isEmpty == false
                || cardioDistance != nil
                || durationMinutes != nil)
    }

    /// The date to pre-fill the confirmation DatePicker with: the parser's
    /// resolved date if present and valid, otherwise today. The user can still
    /// adjust it before saving.
    var resolvedDate: Date {
        if let s = workoutDate, let d = Date.from(apiString: s) { return d }
        return Date()
    }
}

/// Partial update for a session. Only the fields that are set are sent, so the
/// backend (which uses `exclude_unset`) leaves everything else untouched.
/// `exercises` replaces the session's exercise list (used to remove one);
/// `workoutDate` moves the workout to a different day.
struct UpdateSessionRequest: Encodable {
    var workoutDate: String? = nil
    var exercises: [Exercise]? = nil

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(workoutDate, forKey: .workoutDate)
        try c.encodeIfPresent(exercises, forKey: .exercises)
    }

    enum CodingKeys: String, CodingKey {
        case workoutDate, exercises
    }
}

struct CreateSessionRequest: Encodable {
    var deviceUuid: String? = nil
    let workoutDate: String
    let source: String
    let rawTranscript: String?
    let workoutType: String?
    let bodyWeightLbs: Double?
    let cardioNotes: String?
    var cardioActivity: String? = nil
    var cardioDistance: Double? = nil
    var cardioDistanceUnit: String? = nil
    var durationMinutes: Int? = nil
    let sessionNotes: String?
    let exercises: [Exercise]

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encodeIfPresent(deviceUuid, forKey: .deviceUuid)
        try container.encode(workoutDate, forKey: .workoutDate)
        try container.encode(source, forKey: .source)
        try container.encodeIfPresent(rawTranscript, forKey: .rawTranscript)
        try container.encodeIfPresent(workoutType, forKey: .workoutType)
        try container.encodeIfPresent(bodyWeightLbs, forKey: .bodyWeightLbs)
        try container.encodeIfPresent(cardioNotes, forKey: .cardioNotes)
        try container.encodeIfPresent(cardioActivity, forKey: .cardioActivity)
        try container.encodeIfPresent(cardioDistance, forKey: .cardioDistance)
        try container.encodeIfPresent(cardioDistanceUnit, forKey: .cardioDistanceUnit)
        try container.encodeIfPresent(durationMinutes, forKey: .durationMinutes)
        try container.encodeIfPresent(sessionNotes, forKey: .sessionNotes)
        try container.encode(exercises, forKey: .exercises)
    }

    enum CodingKeys: String, CodingKey {
        case deviceUuid = "device_uuid"
        case workoutDate = "workout_date"
        case source
        case rawTranscript = "raw_transcript"
        case workoutType = "workout_type"
        case bodyWeightLbs = "body_weight_lbs"
        case cardioNotes = "cardio_notes"
        case cardioActivity = "cardio_activity"
        case cardioDistance = "cardio_distance"
        case cardioDistanceUnit = "cardio_distance_unit"
        case durationMinutes = "duration_minutes"
        case sessionNotes = "session_notes"
        case exercises
    }
}
