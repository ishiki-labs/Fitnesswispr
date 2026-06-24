import SwiftUI

struct SessionDetailView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var session: WorkoutSession
    @State private var isEditing = false
    @State private var pendingDelete: Exercise?
    @State private var showCardioDeleteConfirm = false
    @State private var error: String?
    @State private var editedDate = Date()
    /// Snapshot taken when entering edit mode so a failed save can roll back.
    @State private var editSnapshot: WorkoutSession?

    /// Called after a successful change so parent screens can refresh.
    private let onChanged: (() -> Void)?
    /// Optimistic callbacks — when provided, parents update their cache without
    /// a full refetch (instant UI). Falls back to `onChanged` otherwise.
    private let onUpdated: ((WorkoutSession) -> Void)?
    private let onDeleted: ((String) -> Void)?

    init(
        session: WorkoutSession,
        onChanged: (() -> Void)? = nil,
        onUpdated: ((WorkoutSession) -> Void)? = nil,
        onDeleted: ((String) -> Void)? = nil
    ) {
        _session = State(initialValue: session)
        self.onChanged = onChanged
        self.onUpdated = onUpdated
        self.onDeleted = onDeleted
    }

    private var canEdit: Bool { ProfileStore.shared.active.canWrite }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    WorkoutTypeBadge(type: session.workoutType)
                    Spacer()
                    if let bw = session.bodyWeightLbs {
                        Label("\(bw, specifier: "%.1f") lbs", systemImage: "scalemass")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                if isEditing {
                    DatePicker(
                        "Date",
                        selection: $editedDate,
                        displayedComponents: .date
                    )
                    .padding(12)
                    .background(Color.cardBackground)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                }

                if let cardio = session.cardioSummaryLine {
                    Label(cardio, systemImage: CardioSummary.symbol)
                        .font(.subheadline.weight(.medium))
                        .foregroundColor(.appAccent)
                }

                if session.isCardioOnly {
                    NavigationLink {
                        CardioProgressView(activity: session.cardioActivity ?? "Cardio")
                    } label: {
                        Label("View \((session.cardioActivity ?? "cardio").lowercased()) progress", systemImage: "chart.line.uptrend.xyaxis")
                            .font(.subheadline.weight(.medium))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(12)
                            .background(Color.cardBackground)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                    }

                    if canEdit {
                        Button(role: .destructive) { showCardioDeleteConfirm = true } label: {
                            Label("Delete this entry", systemImage: "trash")
                                .font(.subheadline.weight(.medium))
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(12)
                                .background(Color.cardBackground)
                                .clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                    }
                }

                exerciseList

                if let error {
                    ErrorBanner(message: error) { self.error = nil }
                }
            }
            .padding()
        }
        .navigationTitle(Date.from(apiString: session.workoutDate)?.displayString ?? session.workoutDate)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            if canEdit && !session.exercises.isEmpty {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(isEditing ? "Done" : "Edit") {
                        if isEditing {
                            saveEdits()
                        } else {
                            editedDate = Date.from(apiString: session.workoutDate) ?? Date()
                            editSnapshot = session
                        }
                        isEditing.toggle()
                    }
                }
            }
        }
        .confirmationDialog(
            "Delete this exercise?",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }
            ),
            presenting: pendingDelete
        ) { exercise in
            Button("Delete \(exercise.name)", role: .destructive) {
                delete(exercise)
            }
            Button("Cancel", role: .cancel) {}
        }
        .confirmationDialog(
            "Delete this \((session.cardioActivity ?? "cardio").lowercased()) entry?",
            isPresented: $showCardioDeleteConfirm,
            titleVisibility: .visible
        ) {
            Button("Delete", role: .destructive) { deleteWholeSession() }
            Button("Cancel", role: .cancel) {}
        }
    }

    @ViewBuilder
    private var exerciseList: some View {
        if isEditing {
            ForEach($session.exercises) { $exercise in
                ExerciseEditCard(exercise: $exercise) {
                    pendingDelete = exercise
                }
            }
        } else {
            ForEach(session.exercises) { exercise in
                ExerciseConfirmCard(exercise: exercise)
            }
        }
    }

    /// Delete the entire session (used for cardio entries, which have no
    /// individual exercises to remove). Optimistic: dismiss immediately.
    private func deleteWholeSession() {
        guard let sessionId = session.sessionId else { return }
        onDeleted?(sessionId)
        onChanged?()
        dismiss()
        Task {
            try? await APIClient.shared.delete(APIEndpoints.session(sessionId))
        }
    }

    /// Persist every edit made in this session (date, exercise names, sets,
    /// reps, weights) in one update. Sends the exercise list only when the
    /// session actually has exercises, so cardio entries just move the date.
    private func saveEdits() {
        guard let sessionId = session.sessionId else { return }
        let previous = editSnapshot ?? session
        editSnapshot = nil

        // Tidy up the edited exercises: sequential set numbers, drop blank sets
        // and blank-named exercises the user cleared out.
        normalizeExercises()

        let newDate = editedDate.apiDateString
        let hasExercises = !session.exercises.isEmpty
        let dateChanged = newDate != previous.workoutDate
        let exercisesChanged = session.exercises != previous.exercises
        guard dateChanged || exercisesChanged else { return }

        // Optimistic: reflect the edits immediately, sync in the background.
        session.workoutDate = newDate
        onUpdated?(session)
        Task {
            do {
                let req = UpdateSessionRequest(
                    workoutDate: newDate,
                    exercises: hasExercises ? session.exercises : nil
                )
                let updated: WorkoutSession = try await APIClient.shared.put(
                    APIEndpoints.session(sessionId), body: req
                )
                session = updated
                onUpdated?(updated)
                onChanged?()
            } catch {
                session = previous
                onUpdated?(previous)
                self.error = "Couldn't save your changes: \(error.localizedDescription)"
            }
        }
    }

    /// Renumber sets sequentially and drop sets/exercises the user emptied out.
    private func normalizeExercises() {
        session.exercises = session.exercises.compactMap { ex in
            var ex = ex
            ex.name = ex.name.trimmingCharacters(in: .whitespacesAndNewlines)
            let kept = ex.sets.filter { $0.reps != nil || $0.weight != nil || $0.durationSeconds != nil }
            ex.sets = (kept.isEmpty ? ex.sets : kept).enumerated().map { idx, set in
                var set = set
                set.setNumber = idx + 1
                return set
            }
            return ex.name.isEmpty ? nil : ex
        }
    }

    private func delete(_ exercise: Exercise) {
        guard let sessionId = session.sessionId else { return }
        pendingDelete = nil
        let previous = session
        let remaining = session.exercises.filter { $0.id != exercise.id }

        if remaining.isEmpty {
            // Last exercise — remove the whole session. Dismiss immediately.
            onDeleted?(sessionId)
            onChanged?()
            dismiss()
            Task {
                do {
                    try await APIClient.shared.delete(APIEndpoints.session(sessionId))
                } catch {
                    // Best-effort; the parent can recover on its next refresh.
                }
            }
            return
        }

        // Optimistic: drop the exercise now, sync in the background.
        session.exercises = remaining
        onUpdated?(session)
        Task {
            do {
                let req = UpdateSessionRequest(exercises: remaining)
                let updated: WorkoutSession = try await APIClient.shared.put(
                    APIEndpoints.session(sessionId), body: req
                )
                session = updated
                onUpdated?(updated)
                onChanged?()
            } catch {
                session = previous
                onUpdated?(previous)
                self.error = "Couldn't delete: \(error.localizedDescription)"
            }
        }
    }
}
