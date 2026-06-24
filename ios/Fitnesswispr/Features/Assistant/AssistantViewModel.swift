import Foundation
import SwiftUI

@MainActor
final class AssistantViewModel: ObservableObject {
    @Published var messages: [ChatMessage] = []
    @Published var composer: String = ""
    @Published var isBusy = false
    @Published var importPreview: ImportPreviewResponse?

    let speech = SpeechRecognizer()
    private let preferences: UserPreferences
    private var bodyWeightLbs: Double?
    /// Exercises the user has logged recently, offered as quick options when we
    /// need to ask which exercise a set belongs to.
    private var recentExercises: [String] = []
    /// Every distinct exercise name the user has logged (original case, most
    /// recent first), used to offer specific variants of a generic name.
    private var allExerciseNames: [String] = []
    /// Most-frequent weights the user has used, per exercise (lowercased name).
    private var weightsByExercise: [String: [Double]] = [:]
    /// Most-frequent set counts and reps across the user's history.
    private var frequentSetCounts: [Int] = []
    private var frequentReps: [Int] = []
    /// Set while we're waiting for the user to supply a missing detail.
    private var pendingClarification: Clarification?
    /// The most recently shown, still-unsaved workout draft. Lets a spoken
    /// correction ("no I meant sled push, not lunges") be applied to it by
    /// re-parsing, instead of being misread as a new message or sent to chat.
    private var lastDraft: (id: UUID, session: ParsedSession)?
    /// The transcript behind the draft currently being clarified — lets us tell
    /// "1x5" (an explicit single set) from a lone set we should ask to confirm.
    private var lastTranscript: String = ""
    /// Fields we've already asked about for the workout currently being logged,
    /// so we never ask twice (and "Bodyweight" answers aren't re-prompted).
    private var askedFields: Set<Clarification.Kind> = []

    private var targetUUID: String { ProfileStore.shared.activeID }

    init(preferences: UserPreferences) {
        self.preferences = preferences
        messages = [greeting]
    }

    private var greeting: ChatMessage {
        let name = ProfileStore.shared.isViewingSelf ? "" : " for \(ProfileStore.shared.active.name)"
        return ChatMessage(
            author: .assistant,
            body: .text(
                "Hey! I'm your SpotRep coach\(name). Tell me what you trained (“bench 3x10 at 135”), tap the mic, or ask me anything — “what's my PR on squat?”"
            )
        )
    }

    func onAppear() {
        Task {
            let url = APIEndpoints.deviceContext(targetUUID)
            if let ctx = try? await APIClient.shared.get(url) as DeviceContextResponse {
                bodyWeightLbs = ctx.lastBodyWeightLbs
            }
        }
        Task { await loadHistory() }
    }

    /// Pull recent sessions to power quick-pick options: recent exercise names,
    /// the weights used per exercise, and the user's typical set/rep counts.
    private func loadHistory() async {
        let end = Date()
        let start = Calendar.current.date(byAdding: .day, value: -120, to: end) ?? end
        let url = APIEndpoints.sessions(
            deviceUUID: targetUUID,
            startDate: start.apiDateString,
            endDate: end.apiDateString,
            limit: 50
        )
        guard let sessions: [WorkoutSession] = try? await APIClient.shared.get(url) else { return }

        var nameOrder: [String] = []
        var nameSeen = Set<String>()
        var weightTally: [String: [Double: Int]] = [:]
        var setTally: [Int: Int] = [:]
        var repTally: [Int: Int] = [:]

        for session in sessions {              // backend returns most-recent first
            for ex in session.exercises {
                let name = ex.name.trimmingCharacters(in: .whitespaces)
                guard !name.isEmpty else { continue }
                let key = name.lowercased()
                if nameSeen.insert(key).inserted { nameOrder.append(name) }
                setTally[ex.sets.count, default: 0] += 1
                for s in ex.sets {
                    if let w = s.weight { weightTally[key, default: [:]][w, default: 0] += 1 }
                    if let r = s.reps { repTally[r, default: 0] += 1 }
                }
            }
        }

        recentExercises = Array(nameOrder.prefix(8))
        allExerciseNames = nameOrder
        weightsByExercise = weightTally.mapValues { tally in
            tally.sorted { $0.value > $1.value }.map(\.key)
        }
        frequentSetCounts = setTally.sorted { $0.value > $1.value }.map(\.key)
        frequentReps = repTally.sorted { $0.value > $1.value }.map(\.key)
    }

    // MARK: - Sending

    func sendComposer() {
        let text = composer.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        composer = ""
        send(text)
    }

    func send(_ text: String) {
        messages.append(ChatMessage(author: .user, body: .text(text)))

        // If we just asked for a missing detail, treat this as the answer —
        // unless the user pivoted to asking a question instead.
        if let pending = pendingClarification, !looksLikeQuestion(text) {
            pendingClarification = nil
            freezeClarification(pending)
            resolveClarification(pending, answer: text)
            return
        }
        pendingClarification = nil

        // A spoken correction of the draft we just showed ("no I meant sled
        // push, not lunges") with no fresh numbers: fix the exercise on that
        // draft by re-parsing the correction with the draft's numbers. Never
        // let it fall through to chat (which would falsely claim to log it).
        if let last = lastDraft, looksLikeCorrection(text),
           !looksLikeQuestion(text), !looksLikeHistoryQuestion(text),
           !looksLikeWorkout(text), !looksLikeCardio(text),
           let ex = last.session.exercises.first {
            isBusy = true
            replace(last.id, with: .thinking)
            askedFields = [.variant, .exercise]   // name is being resolved here
            let transcript = rebuildTranscript(text, from: ex)
            Task {
                await logWorkout(transcript, replacing: last.id,
                                 allowQuestionFallback: false, allowClarify: false)
                isBusy = false
            }
            return
        }
        askedFields = []   // a fresh workout — clear what we've asked about

        let thinkingID = appendThinking()
        isBusy = true
        Task {
            // A message that carries loggable content (sets/reps/weight, or a
            // cardio entry) is a log even when phrased as a request or question —
            // "can you record bench 3x10 at 135?", "can you log ran 2 miles".
            // Only treat it as a question when it reads like one AND has no
            // loggable content.
            let loggable = looksLikeWorkout(text) || looksLikeCardio(text)
            // A history question ("what's my one rep max on bench") is answered
            // even when it mentions an exercise + number; otherwise a message
            // only routes to chat when it reads like a question AND carries no
            // loggable content.
            if looksLikeHistoryQuestion(text) || (looksLikeQuestion(text) && !loggable) {
                await answer(text, replacing: thinkingID)
            } else {
                await logWorkout(text, replacing: thinkingID, allowQuestionFallback: true, allowClarify: true)
            }
            isBusy = false
        }
    }

    /// The user tapped one of the suggested options on a clarification card.
    func chooseClarification(_ option: String, _ clarification: Clarification) {
        messages.append(ChatMessage(author: .user, body: .text(option)))
        pendingClarification = nil
        freezeClarification(clarification)
        resolveClarification(clarification, answer: option)
    }

    /// Apply the user's answer to a pending clarification: re-parse for a missing
    /// exercise name, or fill the chosen number into the draft for weight/reps/sets.
    private func resolveClarification(_ clarification: Clarification, answer: String) {
        switch clarification.kind {
        case .exercise:
            let combined = "\(answer) \(clarification.pendingText)"
            let thinkingID = appendThinking()
            isBusy = true
            Task {
                await logWorkout(combined, replacing: thinkingID, allowQuestionFallback: false)
                isBusy = false
            }
        case .variant:
            guard let draft = clarification.draft, let ex = draft.exercises.first else { return }
            let chosen = answer.trimmingCharacters(in: .whitespacesAndNewlines)
            if looksLikeCorrection(chosen) {
                // A typed correction ("sled push and pull not leg press") — re-parse
                // with the draft's numbers so we store the intended exercise, not the
                // literal correction sentence.
                isBusy = true
                askedFields = [.variant, .exercise]
                let thinkingID = appendThinking()
                Task {
                    await logWorkout(rebuildTranscript(chosen, from: ex), replacing: thinkingID,
                                     allowQuestionFallback: false, allowClarify: false)
                    isBusy = false
                }
            } else {
                var updated = draft
                if !chosen.isEmpty { updated.exercises[0].name = chosen }
                present(updated, in: appendThinking())
            }
        case .weight, .reps, .sets:
            guard var draft = clarification.draft else { return }
            apply(field: clarification.kind, answer: answer, to: &draft)
            present(draft, in: appendThinking())
        }
    }

    /// Replace the interactive clarification card with its plain prompt so its
    /// buttons can no longer be tapped once answered.
    private func freezeClarification(_ clarification: Clarification) {
        replace(clarification.messageID, with: .text(clarification.prompt))
    }

    // MARK: - Routing

    /// Heuristic to decide whether the message is a question vs. a workout to log.
    private func looksLikeQuestion(_ text: String) -> Bool {
        let t = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasSuffix("?") { return true }
        let prefixes = [
            "what", "when", "how", "why", "who", "which", "whats", "what's",
            "did i", "do i", "have i", "show", "tell", "can you", "should i",
            "explain", "summarize", "compare", "am i", "is my", "are my"
        ]
        if prefixes.contains(where: { t.hasPrefix($0) }) { return true }
        let keywords = [
            "my pr", "personal record", " pr ", "last time", "how much",
            "how many", "progress on", "best ", "average", "trend"
        ]
        return keywords.contains(where: { t.contains($0) })
    }

    /// A *history* question asks about past data, so it must be answered even
    /// when it also mentions an exercise + number (e.g. "what's my one rep max
    /// on bench"). Takes precedence over loggable content in the routing gate.
    private func looksLikeHistoryQuestion(_ text: String) -> Bool {
        let t = text.lowercased()
        let keywords = [
            "my pr", "personal record", " pr ", "1rm", "one rep max", "rep max",
            "last time", "how much", "how many", "how often", "progress",
            "best ", "average", "trend", "heaviest", "what's my", "what is my",
            "whats my", "how's my", "hows my", "compared to", "since last",
            "do i usually"
        ]
        return keywords.contains(where: { t.contains($0) })
    }

    /// Voice transcription often spells numbers out ("three sets twelve reps")
    /// rather than using digits, so the workout/cardio guards accept both.
    private static let numberWords: Set<String> = [
        "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen", "twenty", "thirty", "forty",
        "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "couple",
        "few", "dozen"
    ]

    private func hasNumber(_ t: String) -> Bool {
        if t.rangeOfCharacter(from: .decimalDigits) != nil { return true }
        let tokens = t.split { !$0.isLetter }.map(String.init)
        return tokens.contains { Self.numberWords.contains($0) }
    }

    /// A message that fixes the exercise of the draft we just showed rather than
    /// starting a new one — "no I meant ...", "X not Y", "actually ...".
    private func looksLikeCorrection(_ text: String) -> Bool {
        let t = " \(text.lowercased()) "
        let cues = [
            " no i meant ", " i meant ", " not ", " actually ", " instead",
            " i said ", " that's not ", " thats not ", " meant ", " no, "
        ]
        return cues.contains { t.contains($0) }
    }

    /// Rebuild a transcript from a corrected/refined exercise name plus the
    /// draft's existing numbers, so re-parsing yields the intended exercise
    /// (normalised by the parser) instead of a verbatim sentence.
    private func rebuildTranscript(_ name: String, from ex: Exercise) -> String {
        guard let first = ex.sets.first else { return name }
        let count = ex.sets.count
        if let dur = first.durationSeconds {
            return "\(name), \(count) sets of \(dur) seconds"
        }
        var parts = ["\(count) sets"]
        if let r = first.reps { parts.append("of \(r) reps") }
        if let w = first.weight { parts.append("at \(formatNumber(w)) \(first.weightUnit)") }
        return "\(name), " + parts.joined(separator: " ")
    }

    // MARK: - Logging

    private func logWorkout(
        _ text: String,
        replacing id: UUID,
        allowQuestionFallback: Bool,
        allowClarify: Bool = false
    ) async {
        lastTranscript = text
        let context = ParseContext(bodyWeightLbs: bodyWeightLbs)
        let req = ParseRequest(
            transcript: text,
            deviceUuid: targetUUID,
            unitPreference: preferences.unitPreference,
            context: context
        )
        do {
            let parsed: ParsedSession = try await APIClient.shared.post(APIEndpoints.parse, body: req)
            if parsed.exercises.isEmpty {
                if parsed.isCardioOnly {
                    // A standalone cardio entry (e.g. "ran 3 miles") — confirm it.
                    present(parsed, in: id)
                } else if allowClarify, looksLikeWorkout(text) {
                    askExerciseClarification(replacing: id, pendingText: text)
                } else if allowQuestionFallback {
                    await answer(text, replacing: id)
                } else {
                    replace(id, with: .text("I couldn't find a workout in that. Try “incline press 3x8 at 50”."))
                }
                return
            }
            present(parsed, in: id)
        } catch NetworkError.httpError(422, let data) {
            let detail = decodeDetail(data)
            // A workout that's only missing its exercise name — ask, don't give up.
            if allowClarify, looksLikeWorkout(text), needsExerciseName(detail) {
                askExerciseClarification(replacing: id, pendingText: text)
            } else if allowQuestionFallback {
                await answer(text, replacing: id)
            } else {
                replace(id, with: .text(detail ?? "I couldn't parse that as a workout."))
            }
        } catch {
            replace(id, with: .text("Something went wrong: \(error.localizedDescription)"))
        }
    }

    /// Show the draft for confirmation, or — if a key detail is still missing —
    /// ask for it first with quick options. Fields are asked one at a time.
    private func present(_ draft: ParsedSession, in slotID: UUID) {
        guard let field = firstMissingField(draft) else {
            replace(slotID, with: .workoutDraft(draft))
            lastDraft = (slotID, draft)   // a savable draft a correction can target
            return
        }
        let clarification = Clarification(
            messageID: slotID,
            kind: field,
            prompt: prompt(for: field, draft: draft),
            options: options(for: field, draft: draft),
            pendingText: "",
            draft: draft
        )
        askedFields.insert(field)
        replace(slotID, with: .clarify(clarification))
        pendingClarification = clarification
    }

    /// The first detail that's missing from the (first) exercise, in priority
    /// order, skipping anything we've already asked about for this workout.
    private func firstMissingField(_ draft: ParsedSession) -> Clarification.Kind? {
        guard let ex = draft.exercises.first, !ex.sets.isEmpty else { return nil }
        if !askedFields.contains(.variant), !variantSuggestions(for: ex.name).isEmpty { return .variant }
        if ex.sets.contains(where: { $0.durationSeconds != nil }) { return nil } // timed hold
        let hasWeight = ex.sets.contains { $0.weight != nil }
        let hasReps = ex.sets.contains { $0.reps != nil }
        let bodyweight = (ex.equipment?.lowercased() == "bodyweight")
            || (ex.equipment?.lowercased() == "body weight")
            || isBodyweight(ex.name)
        if !askedFields.contains(.weight), !hasWeight, !bodyweight { return .weight }
        // Carries/sleds/yoke are measured by distance, not reps — don't nag for reps.
        if !askedFields.contains(.reps), !hasReps, !isDistanceBased(ex.name) { return .reps }
        // Only confirm a lone set when the user didn't actually state a count
        // ("1x5" / "1 set" is a deliberate single set, not a missing detail).
        if !askedFields.contains(.sets), ex.sets.count == 1, !messageStatesSetCount(lastTranscript) { return .sets }
        return nil
    }

    private func prompt(for field: Clarification.Kind, draft: ParsedSession?) -> String {
        let name = draft?.exercises.first?.name ?? "that"
        switch field {
        case .exercise: return "Got the sets — which exercise was that? Tap one or type it."
        case .variant:  return "Which \(name.lowercased())? Tap one or type it."
        case .weight:   return "What weight for \(name)? Tap one or type it."
        case .reps:     return "How many reps? Tap one or type it."
        case .sets:     return "How many sets? Tap one or type it."
        }
    }

    private func options(for field: Clarification.Kind, draft: ParsedSession?) -> [String] {
        switch field {
        case .exercise:
            return recentExercises.isEmpty
                ? ["Bench Press", "Squat", "Deadlift", "Overhead Press", "Lat Pulldown"]
                : Array(recentExercises.prefix(6))
        case .variant:
            let name = draft?.exercises.first?.name ?? ""
            var opts = Array(variantSuggestions(for: name).prefix(5))
            // Keep the generic term itself as an option.
            if !opts.contains(where: { $0.caseInsensitiveCompare(name) == .orderedSame }) {
                opts.append(name)
            }
            return opts
        case .weight:
            let name = draft?.exercises.first?.name ?? ""
            let unit = draft?.exercises.first?.sets.first?.weightUnit ?? preferences.unitPreference
            var opts = weightSuggestions(for: name).prefix(3).map { "\(formatNumber($0)) \(unit)" }
            opts.append("Bodyweight")
            return Array(opts)
        case .reps:
            let freq = frequentReps.filter { $0 > 0 }.prefix(3).map(String.init)
            return freq.isEmpty ? ["8", "10", "12"] : Array(freq)
        case .sets:
            let freq = frequentSetCounts.filter { $0 > 1 }.prefix(3).map(String.init)
            return freq.isEmpty ? ["3", "4", "5"] : Array(freq)
        }
    }

    /// Apply a numeric clarification answer to the draft's first exercise.
    private func apply(field: Clarification.Kind, answer: String, to draft: inout ParsedSession) {
        guard !draft.exercises.isEmpty else { return }
        let number = firstNumber(in: answer)
        switch field {
        case .weight:
            guard let w = number else { return } // e.g. "Bodyweight" → leave unweighted
            let unit = draft.exercises[0].sets.first?.weightUnit ?? preferences.unitPreference
            for i in draft.exercises[0].sets.indices {
                draft.exercises[0].sets[i].weight = w
                draft.exercises[0].sets[i].weightUnit = unit
            }
        case .reps:
            guard let r = number else { return }
            for i in draft.exercises[0].sets.indices {
                draft.exercises[0].sets[i].reps = Int(r)
            }
        case .sets:
            guard let n = number, n >= 1 else { return }
            let count = min(Int(n), 20)
            let template = draft.exercises[0].sets.first
            draft.exercises[0].sets = (1...count).map { i in
                ExerciseSet(
                    setNumber: i,
                    reps: template?.reps,
                    weight: template?.weight,
                    weightUnit: template?.weightUnit ?? preferences.unitPreference,
                    durationSeconds: template?.durationSeconds
                )
            }
        case .exercise, .variant:
            break
        }
    }

    /// Exercises that are normally done without external weight, so we shouldn't
    /// nag for a weight value.
    private func isBodyweight(_ name: String) -> Bool {
        let n = name.lowercased()
        let bodyweight = [
            "push up", "pushup", "push-up", "pull up", "pullup", "pull-up",
            "chin up", "chinup", "dip", "plank", "sit up", "situp", "sit-up",
            "crunch", "burpee", "mountain climber", "leg raise", "knee raise",
            "hanging", "box jump", "jump squat", "squat jump", "jumping jack",
            "broad jump", "tuck jump", "lunge jump", "split jump", "star jump",
            "wall sit", "glute bridge", "hip bridge", "superman", "bird dog",
            "flutter kick", "v-up", "v up", "hollow", "l-sit", "l sit",
            "dead hang", "pistol squat", "sissy squat", "air squat", "bodyweight",
            "body weight", "high knee", "bear crawl", "inchworm", "handstand",
            "skater", "toes to bar", "calf raise", "lunge", "inverted row",
            "tire flip", "sledgehammer", "nordic", "pike push", "muscle up",
            "ring row", "ring dip"
        ]
        return bodyweight.contains { n.contains($0) }
    }

    /// Carries, sleds, drags, and yoke walks are measured by load + distance,
    /// not reps — so a missing rep count is expected, not something to ask about.
    private func isDistanceBased(_ name: String) -> Bool {
        let n = name.lowercased()
        let kws = ["carry", "carries", "farmer", "suitcase", "sled", "drag",
                   "prowler", "yoke", "sandbag"]
        return kws.contains { n.contains($0) }
    }

    /// Did the user explicitly state how many sets? ("3x10", "1x5", "3 sets",
    /// "one set", "single set"). If so we trust the parsed set count and don't
    /// ask them to confirm a lone set.
    private func messageStatesSetCount(_ text: String) -> Bool {
        let t = text.lowercased()
        if t.contains("single set") || t.contains("one set") { return true }
        let patterns = [
            #"\b\d+\s*[x×]\s*\d+"#,
            #"\b\d+\s*sets?\b"#,
            #"\b(one|two|three|four|five|six|seven|eight|nine|ten|single|couple|few)\s+sets?\b"#
        ]
        return patterns.contains { t.range(of: $0, options: .regularExpression) != nil }
    }

    /// Weights to suggest for an exercise. Falls back to fuzzy matching so that
    /// typing "squats" still surfaces the weights logged for "goblet squat".
    private func weightSuggestions(for name: String) -> [Double] {
        let key = name.lowercased().trimmingCharacters(in: .whitespaces)
        if let exact = weightsByExercise[key], !exact.isEmpty { return exact }

        let queryTokens = movementTokens(key)
        guard !queryTokens.isEmpty else { return [] }

        // Rank history exercises by how well they overlap with what was typed,
        // then flatten their weights (best match first) and de-duplicate.
        let ranked = weightsByExercise
            .compactMap { (exKey, weights) -> (weights: [Double], score: Int)? in
                guard !weights.isEmpty else { return nil }
                let shared = queryTokens.intersection(movementTokens(exKey)).count
                let containment = (exKey.contains(key) || key.contains(exKey)) ? 1 : 0
                let score = shared * 2 + containment
                return score > 0 ? (weights, score) : nil
            }
            .sorted { $0.score > $1.score }

        var seen = Set<Double>()
        var result: [Double] = []
        for w in ranked.flatMap(\.weights) where seen.insert(w).inserted { result.append(w) }
        return result
    }

    /// More specific variants of a (possibly generic) exercise name found in the
    /// user's history. Typing "squats" surfaces "Goblet Squat", "Hack Squat", …
    private func variantSuggestions(for name: String) -> [String] {
        let query = movementTokens(name)
        guard !query.isEmpty else { return [] }
        let key = name.lowercased().trimmingCharacters(in: .whitespaces)
        return allExerciseNames.filter { candidate in
            let candidateKey = candidate.lowercased().trimmingCharacters(in: .whitespaces)
            guard candidateKey != key else { return false }
            let tokens = movementTokens(candidate)
            // Same movement, but a more specific phrasing (e.g. "goblet squat").
            return query.isSubset(of: tokens) && tokens.count > query.count
        }
    }

    /// Lower-cased movement words for fuzzy matching, dropping equipment/filler
    /// words and crudely singularising ("squats" -> "squat").
    private func movementTokens(_ s: String) -> Set<String> {
        let filler: Set<String> = [
            "the", "a", "with", "and", "of", "on", "machine",
            "barbell", "dumbbell", "cable", "smith"
        ]
        let tokens = s.split { !$0.isLetter }
            .map { String($0).lowercased() }
            .map { $0.count > 3 && $0.hasSuffix("s") ? String($0.dropLast()) : $0 }
            .filter { !$0.isEmpty && !filler.contains($0) }
        return Set(tokens)
    }

    private func firstNumber(in text: String) -> Double? {
        guard let range = text.range(of: #"\d+(\.\d+)?"#, options: .regularExpression) else { return nil }
        return Double(text[range])
    }

    private func formatNumber(_ value: Double) -> String {
        value == value.rounded() ? String(Int(value)) : String(value)
    }

    /// Turn the in-flight "thinking" bubble into a question asking which exercise
    /// the sets belong to.
    private func askExerciseClarification(replacing id: UUID, pendingText: String) {
        let clarification = Clarification(
            messageID: id,
            kind: .exercise,
            prompt: prompt(for: .exercise, draft: nil),
            options: options(for: .exercise, draft: nil),
            pendingText: pendingText,
            draft: nil
        )
        askedFields.insert(.exercise)
        replace(id, with: .clarify(clarification))
        pendingClarification = clarification
    }

    private func decodeDetail(_ data: Data) -> String? {
        (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
    }

    private func needsExerciseName(_ detail: String?) -> Bool {
        guard let detail else { return true } // no detail → assume the name is what's missing
        return detail.lowercased().contains("exercise name")
    }

    /// Heuristic: does this read like a set of reps/sets/weight (so a missing
    /// piece is worth asking about) rather than free-form chatter?
    private func looksLikeWorkout(_ text: String) -> Bool {
        let t = text.lowercased()
        guard hasNumber(t) else { return false }
        let signals = [
            "rep", "set", "lbs", " lb", "kg", "pound", "kilo", "@",
            "second", "minute", "hold"
        ]
        if signals.contains(where: { t.contains($0) }) { return true }
        return t.range(of: #"\d\s*[x×]\s*\d"#, options: .regularExpression) != nil
    }

    /// Cardio entries ("ran 3 miles", "30 minute run") carry no set/rep/weight
    /// signals, so `looksLikeWorkout` misses them. Used in the routing gate so a
    /// polite request ("can you log ran 2 miles") still gets logged, not chatted.
    private func looksLikeCardio(_ text: String) -> Bool {
        let t = text.lowercased()
        guard hasNumber(t) else { return false }
        let signals = [
            "ran ", "run", "running", "jog", "sprint", "treadmill", "cycl",
            "bike", "biked", "row", "swam", "swim", "walk", "hiit", "cardio",
            "mile", "km", "marathon", "elliptical"
        ]
        return signals.contains { t.contains($0) }
    }

    func saveDraft(_ parsed: ParsedSession, date: Date, draftID: UUID) {
        if lastDraft?.id == draftID { lastDraft = nil }
        // Optimistic: confirm in the chat immediately, save in the background.
        let count = parsed.exercises.count
        let confirmation: String
        if count == 0, parsed.isCardioOnly {
            confirmation = "Logged \(parsed.cardioActivity ?? "cardio") ✓"
        } else {
            confirmation = "Logged \(count) exercise\(count == 1 ? "" : "s") ✓"
        }
        replace(draftID, with: .saved(confirmation))

        let req = CreateSessionRequest(
            deviceUuid: targetUUID,
            workoutDate: date.apiDateString,
            source: "assistant",
            rawTranscript: nil,
            workoutType: parsed.workoutType,
            bodyWeightLbs: parsed.bodyWeightLbs,
            cardioNotes: parsed.cardioNotes,
            cardioActivity: parsed.cardioActivity,
            cardioDistance: parsed.cardioDistance,
            cardioDistanceUnit: parsed.cardioDistanceUnit,
            durationMinutes: parsed.durationMinutes,
            sessionNotes: nil,
            exercises: parsed.exercises
        )
        Task {
            do {
                let saved: WorkoutSession = try await APIClient.shared.post(APIEndpoints.sessions, body: req)
                // Notify with the saved session so listeners can update instantly.
                NotificationCenter.default.post(name: .workoutLogged, object: saved)
            } catch {
                appendAssistant(.text("Hmm, that didn't save — \(error.localizedDescription). Want to try again?"))
            }
        }
    }

    func discardDraft(_ draftID: UUID) {
        if lastDraft?.id == draftID { lastDraft = nil }
        replace(draftID, with: .text("No worries — nothing was saved."))
    }

    // MARK: - Questions

    private func answer(_ question: String, replacing id: UUID) async {
        let req = AssistantChatRequest(
            deviceUuid: targetUUID,
            message: question,
            history: recentTurns(excluding: question)
        )
        do {
            let resp: AssistantChatResponse = try await APIClient.shared.post(APIEndpoints.assistantChat, body: req)
            replace(id, with: .text(resp.reply.isEmpty ? "I'm not sure about that one." : resp.reply))
        } catch {
            replace(id, with: .text("I couldn't reach the coach right now. \(error.localizedDescription)"))
        }
    }

    /// The recent chat transcript (plain text turns, oldest first) sent with a
    /// question so the assistant can resolve follow-ups. Excludes the current
    /// question (just appended) and the empty "thinking" placeholder.
    private func recentTurns(excluding question: String) -> [ChatTurn] {
        var turns: [ChatTurn] = []
        for m in messages {
            let content: String
            switch m.body {
            case .text(let t): content = t
            case .saved(let t): content = t
            default: continue
            }
            guard !content.isEmpty else { continue }
            turns.append(ChatTurn(role: m.author == .user ? "user" : "assistant", content: content))
        }
        if let last = turns.last, last.role == "user", last.content == question {
            turns.removeLast()
        }
        return Array(turns.suffix(8))
    }

    // MARK: - Voice

    func startVoice() {
        Task {
            let granted = await speech.requestPermission()
            guard granted else {
                appendAssistant(.text("I need microphone and speech permission to listen."))
                return
            }
            speech.start()
        }
    }

    func stopVoiceAndSend() {
        let text = speech.stop().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        send(text)
    }

    // MARK: - Import

    func startImport(kind: String, data: Data, filename: String?, mime: String?) {
        let label = kind == "spreadsheet" ? "spreadsheet" : "photo"
        messages.append(ChatMessage(author: .user, body: .text("📎 \(filename ?? label)")))
        let thinkingID = appendThinking()
        isBusy = true
        let b64 = data.base64EncodedString()
        Task {
            do {
                let req = ImportPreviewRequest(kind: kind, contentBase64: b64, filename: filename, mime: mime)
                // Spreadsheet extraction can take ~30–60s; allow plenty of time.
                let resp: ImportPreviewResponse = try await APIClient.shared.post(
                    APIEndpoints.importPreview, body: req, timeout: 180
                )
                replace(thinkingID, with: .text("I found \(resp.summary). Review the details and import below."))
                importPreview = resp
            } catch NetworkError.httpError(422, let data) {
                let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["detail"]
                    ?? "I couldn't read that file."
                replace(thinkingID, with: .text(msg))
            } catch {
                replace(thinkingID, with: .text("Import failed: \(error.localizedDescription)"))
            }
            isBusy = false
        }
    }

    func commitImport(items: [ImportCommitItem]) {
        importPreview = nil
        guard !items.isEmpty else {
            appendAssistant(.text("Nothing was selected to import."))
            return
        }
        let thinkingID = appendThinking()
        isBusy = true
        Task {
            do {
                let resp: ImportCommitResponse = try await APIClient.shared.post(
                    APIEndpoints.importCommit, body: ImportCommitRequest(items: items), timeout: 60
                )
                replace(thinkingID, with: .saved("Imported \(resp.created) workouts ✓"))
                NotificationCenter.default.post(name: .workoutLogged, object: nil)
            } catch {
                replace(thinkingID, with: .text("Couldn't import: \(error.localizedDescription)"))
            }
            isBusy = false
        }
    }

    // MARK: - Message helpers

    private func appendThinking() -> UUID {
        let msg = ChatMessage(author: .assistant, body: .thinking)
        messages.append(msg)
        return msg.id
    }

    private func appendAssistant(_ body: ChatMessage.Body) {
        messages.append(ChatMessage(author: .assistant, body: body))
    }

    private func replace(_ id: UUID, with body: ChatMessage.Body) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else {
            appendAssistant(body)
            return
        }
        messages[idx].body = body
    }
}

extension Notification.Name {
    static let workoutLogged = Notification.Name("spotrep.workoutLogged")
}
