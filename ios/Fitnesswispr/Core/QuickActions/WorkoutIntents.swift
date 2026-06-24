import AppIntents
import Foundation

// MARK: - Open app and start recording (long-press icon / Action Button / Siri)

struct RecordWorkoutIntent: AppIntent {
    static var title: LocalizedStringResource = "Record a Workout"
    static var description = IntentDescription("Open SpotRep and start recording a workout by voice.")
    static var openAppWhenRun: Bool = true

    @MainActor
    func perform() async throws -> some IntentResult {
        QuickActionCoordinator.shared.triggerRecordNow()
        return .result()
    }
}

// MARK: - Hands-free logging (Siri asks "what did you do?", parses + saves, no UI)

struct LogWorkoutIntent: AppIntent {
    static var title: LocalizedStringResource = "Log a Workout"
    static var description = IntentDescription("Say your sets and reps and SpotRep logs them for today.")
    static var openAppWhenRun: Bool = false

    @Parameter(title: "Workout", requestValueDialog: "What did you do?")
    var workout: String

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let summary = try await QuickLogService().logWorkout(transcript: workout)
        return .result(dialog: IntentDialog(stringLiteral: summary))
    }
}

// MARK: - Siri / Shortcuts phrases

struct SpotRepShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: RecordWorkoutIntent(),
            phrases: [
                "Record a workout in \(.applicationName)",
                "Start recording in \(.applicationName)"
            ],
            shortTitle: "Record Workout",
            systemImageName: "mic.fill"
        )
        AppShortcut(
            intent: LogWorkoutIntent(),
            phrases: [
                "Log a workout in \(.applicationName)",
                "Log my workout in \(.applicationName)"
            ],
            shortTitle: "Log Workout",
            systemImageName: "checklist"
        )
    }
}

// MARK: - Backend parse + save used by the hands-free intent

struct QuickLogService {
    @MainActor
    func logWorkout(transcript: String) async throws -> String {
        let trimmed = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return "I didn't catch any workout to log." }

        var bodyWeight: Double?
        if let ctx = try? await APIClient.shared.get(
            APIEndpoints.deviceContext(Identity.current)
        ) as DeviceContextResponse {
            bodyWeight = ctx.lastBodyWeightLbs
        }

        let unit = UserDefaults.standard.string(forKey: "unit_preference") ?? "lbs"
        let parseBody = ParseRequest(
            transcript: trimmed,
            deviceUuid: Identity.current,
            unitPreference: unit,
            context: ParseContext(bodyWeightLbs: bodyWeight)
        )

        let parsed: ParsedSession
        do {
            parsed = try await APIClient.shared.post(APIEndpoints.parse, body: parseBody)
        } catch NetworkError.httpError(422, _) {
            return "I couldn't understand that workout. Try again with the exercise, weight, and reps."
        }

        let req = CreateSessionRequest(
            workoutDate: parsed.resolvedDate.apiDateString,
            source: "siri",
            rawTranscript: trimmed,
            workoutType: parsed.workoutType,
            bodyWeightLbs: parsed.bodyWeightLbs,
            cardioNotes: parsed.cardioNotes,
            sessionNotes: nil,
            exercises: parsed.exercises
        )
        let _: WorkoutSession = try await APIClient.shared.post(APIEndpoints.sessions, body: req)

        let count = parsed.exercises.count
        let type = parsed.workoutType ?? "workout"
        if count == 0 {
            return "Logged your \(type) workout."
        }
        let exWord = count == 1 ? "exercise" : "exercises"
        return "Logged your \(type) workout with \(count) \(exWord)."
    }
}
