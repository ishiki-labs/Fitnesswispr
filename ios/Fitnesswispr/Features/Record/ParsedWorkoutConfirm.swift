import SwiftUI

struct ParsedWorkoutConfirm: View {
    let parsed: ParsedSession
    let onSave: (Date) -> Void
    let onRetry: () -> Void

    @State private var workoutDate: Date

    init(parsed: ParsedSession, onSave: @escaping (Date) -> Void, onRetry: @escaping () -> Void) {
        self.parsed = parsed
        self.onSave = onSave
        self.onRetry = onRetry
        _workoutDate = State(initialValue: parsed.resolvedDate)
    }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if let type = parsed.workoutType {
                        WorkoutTypeBadge(type: type)
                    }

                    DatePicker("Workout Date", selection: $workoutDate, displayedComponents: .date)
                        .datePickerStyle(.compact)

                    if let bw = parsed.bodyWeightLbs {
                        Label("Body weight: \(bw, specifier: "%.1f") lbs", systemImage: "scalemass")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }

                    if let cardio = parsed.cardioNotes {
                        Label(cardio, systemImage: "figure.run")
                            .font(.subheadline)
                            .foregroundColor(.secondary)
                    }

                    ForEach(parsed.exercises) { exercise in
                        ExerciseConfirmCard(exercise: exercise)
                    }

                    VStack(spacing: 12) {
                        PrimaryButton(title: "Save Workout") {
                            onSave(workoutDate)
                        }
                        Button("Try Again", action: onRetry)
                            .foregroundColor(.secondary)
                    }
                    .padding(.top, 8)
                }
                .padding()
            }
            .navigationTitle("Confirm Workout")
        }
    }
}
