import Foundation
import Speech
import AVFoundation

@MainActor
final class RecordViewModel: ObservableObject {
    enum State {
        case idle
        case recording
        case parsing
        case confirming(ParsedSession)
        case saved
        case error(String)
    }

    @Published var state: State = .idle
    @Published var transcript: String = ""
    @Published var audioLevels: [Float] = Array(repeating: 0, count: 30)

    private var sessionContext = SessionContext()
    private var preferences: UserPreferences

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let audioEngine = AVAudioEngine()

    init(preferences: UserPreferences) {
        self.preferences = preferences
    }

    func onAppear() {
        Task { await fetchContext() }
    }

    /// The profile this recording is being logged for (you or a linked profile
    /// you have write access to).
    private var targetUUID: String { ProfileStore.shared.activeID }

    private func fetchContext() async {
        let url = APIEndpoints.deviceContext(targetUUID)
        if let ctx = try? await APIClient.shared.get(url) as DeviceContextResponse {
            sessionContext.bodyWeightLbs = ctx.lastBodyWeightLbs
        }
    }

    func requestPermissions() async -> Bool {
        let speechStatus = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        guard speechStatus == .authorized else { return false }

        let audioStatus = await AVAudioApplication.requestRecordPermission()
        return audioStatus
    }

    func startRecording() {
        guard case .idle = state else { return }
        transcript = ""
        state = .recording

        let audioSession = AVAudioSession.sharedInstance()
        try? audioSession.setCategory(.record, mode: .measurement, options: .duckOthers)
        try? audioSession.setActive(true, options: .notifyOthersOnDeactivation)

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        recognitionRequest = request

        recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, _ in
            Task { @MainActor [weak self] in
                if let result = result {
                    self?.transcript = result.bestTranscription.formattedString
                }
            }
        }

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)

            let channelData = buffer.floatChannelData?[0]
            let frameLength = Int(buffer.frameLength)
            let rms: Float = channelData.map { ptr in
                var sum: Float = 0
                for i in 0..<frameLength { sum += ptr[i] * ptr[i] }
                return sqrt(sum / Float(max(frameLength, 1)))
            } ?? 0
            let normalized = min(1.0, rms * 10)

            Task { @MainActor [weak self] in
                guard let self else { return }
                var levels = self.audioLevels
                levels.removeFirst()
                levels.append(normalized)
                self.audioLevels = levels
            }
        }

        audioEngine.prepare()
        try? audioEngine.start()
    }

    func stopRecording() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest = nil

        let capturedTranscript = transcript
        state = .parsing

        Task {
            await parseTranscript(capturedTranscript)
        }
    }

    private func parseTranscript(_ text: String) async {
        guard !text.isEmpty else {
            state = .error("No speech detected")
            return
        }

        let context = ParseContext(bodyWeightLbs: sessionContext.bodyWeightLbs)
        let body = ParseRequest(
            transcript: text,
            deviceUuid: targetUUID,
            unitPreference: preferences.unitPreference,
            context: context
        )

        do {
            let parsed: ParsedSession = try await APIClient.shared.post(APIEndpoints.parse, body: body)
            state = .confirming(parsed)
        } catch NetworkError.httpError(422, let data) {
            let msg = (try? JSONDecoder().decode([String: String].self, from: data))?["error"] ?? "Could not parse workout"
            state = .error(msg)
        } catch {
            state = .error(error.localizedDescription)
        }
    }

    func confirmAndSave(parsed: ParsedSession, workoutDate: Date) {
        sessionContext.merge(from: parsed)
        state = .parsing

        Task {
            let req = CreateSessionRequest(
                deviceUuid: targetUUID,
                workoutDate: workoutDate.apiDateString,
                source: "voice",
                rawTranscript: transcript,
                workoutType: parsed.workoutType,
                bodyWeightLbs: parsed.bodyWeightLbs,
                cardioNotes: parsed.cardioNotes,
                sessionNotes: nil,
                exercises: parsed.exercises
            )
            do {
                let _: WorkoutSession = try await APIClient.shared.post(APIEndpoints.sessions, body: req)
                state = .saved
            } catch {
                state = .error(error.localizedDescription)
            }
        }
    }

    func reset() {
        state = .idle
        transcript = ""
        audioLevels = Array(repeating: 0, count: 30)
    }

    /// Tears down any in-progress capture without parsing — used when the
    /// recorder sheet is dismissed mid-recording.
    func cancelRecording() {
        if case .recording = state {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
            recognitionRequest?.endAudio()
            recognitionTask?.cancel()
            recognitionRequest = nil
            recognitionTask = nil
        }
        state = .idle
        transcript = ""
        audioLevels = Array(repeating: 0, count: 30)
    }
}

struct ParseContext: Encodable {
    let bodyWeightLbs: Double?
    /// The device's LOCAL today (YYYY-MM-DD) so the parser resolves relative
    /// dates ("yesterday", "last friday") in the user's timezone.
    var today: String = Date().apiDateString

    enum CodingKeys: String, CodingKey {
        case bodyWeightLbs = "body_weight_lbs"
        case today
    }
}

struct ParseRequest: Encodable {
    let transcript: String
    let deviceUuid: String
    let unitPreference: String
    let context: ParseContext

    enum CodingKeys: String, CodingKey {
        case transcript
        case deviceUuid = "device_uuid"
        case unitPreference = "unit_preference"
        case context
    }
}
