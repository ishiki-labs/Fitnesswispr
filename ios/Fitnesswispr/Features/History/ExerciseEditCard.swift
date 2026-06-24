import SwiftUI

/// Editable counterpart to `ExerciseConfirmCard`: lets the user change the
/// exercise name and each set's reps/weight (or duration for timed holds), and
/// add or remove sets. Used by `SessionDetailView` while editing.
struct ExerciseEditCard: View {
    @Binding var exercise: Exercise
    var onDelete: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                TextField("Exercise name", text: $exercise.name)
                    .font(.headline)
                    .textInputAutocapitalization(.words)
                    .submitLabel(.done)
                Spacer(minLength: 4)
                Button(action: onDelete) {
                    Image(systemName: "minus.circle.fill")
                        .font(.title3)
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, .red)
                }
                .accessibilityLabel("Remove \(exercise.name)")
            }

            Divider()

            ForEach($exercise.sets) { $set in
                HStack(spacing: 8) {
                    Text("Set \(set.setNumber)")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                        .frame(width: 52, alignment: .leading)
                    Spacer(minLength: 0)
                    if set.durationSeconds != nil {
                        numberField("0", text: intText($set.durationSeconds), width: 56)
                        Text("sec").font(.caption).foregroundColor(.secondary)
                    } else {
                        numberField("0", text: intText($set.reps), width: 48)
                        Text("reps").font(.caption).foregroundColor(.secondary)
                        numberField("0", text: doubleText($set.weight), width: 64, decimal: true)
                        Text(set.weightUnit).font(.caption).foregroundColor(.secondary)
                    }
                    if exercise.sets.count > 1 {
                        Button { removeSet(set) } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundColor(.secondary)
                        }
                        .accessibilityLabel("Remove set \(set.setNumber)")
                    }
                }
                .font(.subheadline)
            }

            Button(action: addSet) {
                Label("Add set", systemImage: "plus.circle")
                    .font(.subheadline.weight(.medium))
            }
            .padding(.top, 2)
        }
        .padding()
        .background(Color.cardBackground)
        .clipShape(RoundedRectangle(cornerRadius: 12))
    }

    // MARK: - Set mutations

    private func addSet() {
        let last = exercise.sets.last
        let next = ExerciseSet(
            setNumber: exercise.sets.count + 1,
            reps: last?.reps,
            weight: last?.weight,
            weightUnit: last?.weightUnit ?? "lbs",
            durationSeconds: last?.durationSeconds
        )
        exercise.sets.append(next)
    }

    private func removeSet(_ set: ExerciseSet) {
        exercise.sets.removeAll { $0.setNumber == set.setNumber }
        renumber()
    }

    private func renumber() {
        for i in exercise.sets.indices { exercise.sets[i].setNumber = i + 1 }
    }

    // MARK: - Editable numeric field

    @ViewBuilder
    private func numberField(_ placeholder: String, text: Binding<String>, width: CGFloat, decimal: Bool = false) -> some View {
        TextField(placeholder, text: text)
            .keyboardType(decimal ? .decimalPad : .numberPad)
            .multilineTextAlignment(.trailing)
            .frame(width: width)
            .padding(.vertical, 6)
            .padding(.horizontal, 8)
            .background(Color.primary.opacity(0.06))
            .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    // MARK: - Optional-number <-> String bindings

    private func intText(_ binding: Binding<Int?>) -> Binding<String> {
        Binding(
            get: { binding.wrappedValue.map(String.init) ?? "" },
            set: { binding.wrappedValue = Int($0.filter(\.isNumber)) }
        )
    }

    private func doubleText(_ binding: Binding<Double?>) -> Binding<String> {
        Binding(
            get: {
                guard let v = binding.wrappedValue else { return "" }
                return v == v.rounded() ? String(Int(v)) : String(v)
            },
            set: { binding.wrappedValue = Double($0) }
        )
    }
}
