// Records a meeting as two separate audio tracks:
//   them.m4a — everything the meeting app plays through your speakers
//   me.m4a   — your microphone
//
// Keeping the tracks apart is what gives the transcript speaker labels
// without any speaker-identification model.
//
// Usage: recorder <output-dir>     Stops on Ctrl-C (SIGINT) or SIGTERM.
//        recorder --self-test      Runs the pure checks and exits.

import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

// MARK: - One shared time origin

/// The single source time both tracks start their writing session at.
///
/// System audio and microphone samples never arrive at the same instant. If
/// each writer began its session at its own first timestamp, every entry in
/// me.json would sit a few hundred milliseconds away from them.json, and
/// merge.py would interleave the two transcripts in the wrong order. So the
/// first sample to arrive on either track fixes the origin for both.
///
/// The two tracks run on two sample handler queues, so the claim is locked.
final class SharedStart {
    private let lock = NSLock()
    private var origin: CMTime?

    /// Returns the shared origin, claiming it for `pts` if nobody has yet.
    func claim(_ pts: CMTime) -> CMTime {
        lock.lock()
        defer { lock.unlock() }
        if let origin { return origin }
        origin = pts
        return pts
    }
}

/// What a writer must do with one sample. Pure so `--self-test` can drive it.
struct SampleAction: Equatable {
    /// Non-nil when the writer must open its session at this source time first.
    let startSessionAt: CMTime?
    /// False when the sample cannot go into the file and must be counted lost.
    let writeSample: Bool
}

/// Decides the action for one sample.
///
/// `sessionStart` is nil until this writer has opened its session.
/// `sharedOrigin` is the value `SharedStart.claim` returned for this sample,
/// and is only read on the first sample of the track.
///
/// A track that opens second can hold a sample older than the origin. Such a
/// sample cannot be written, because the session already begins later. Losing
/// that one sample keeps both files on the same clock, which is the point.
func sampleAction(pts: CMTime, sessionStart: CMTime?, sharedOrigin: CMTime?) -> SampleAction {
    guard pts.isNumeric else {
        return SampleAction(startSessionAt: nil, writeSample: false)
    }
    if let sessionStart {
        return SampleAction(startSessionAt: nil, writeSample: pts >= sessionStart)
    }
    let start = sharedOrigin ?? pts
    return SampleAction(startSessionAt: start, writeSample: pts >= start)
}

// MARK: - One audio track on disk

/// How many samples a track kept and how many it lost.
struct TrackStats {
    var appended = 0
    /// The encoder was not ready, or the writer was not in a writable state.
    var dropped = 0
    /// The sample was older than the shared origin.
    var early = 0
}

/// Describes sample loss on one track, or nil when the track kept everything.
///
/// A lost sample keeps its presentation timestamp, so the hole sounds like
/// silence instead of shortening the file. No downstream health check can see
/// that, which is why the loss has to be reported here.
func lossReport(track: String, stats: TrackStats) -> String? {
    let lost = stats.dropped + stats.early
    guard lost > 0 else { return nil }
    let total = stats.appended + lost
    let percent = Double(lost) * 100 / Double(total)
    let counts = String(format: "%@: lost %d of %d samples (%.1f%%)", track, lost, total, percent)
    // Below one percent the holes are single frames and nobody will hear them.
    // Above it the transcript is missing words, and the user must be told.
    if percent >= 1 {
        return "warning: \(counts) — that audio is gone and the transcript will have gaps"
    }
    return "\(counts) — brief holes, transcript should be intact"
}

final class TrackWriter {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let queue = DispatchQueue(label: "qn.track")
    private let shared: SharedStart
    private var sessionStart: CMTime?
    private var counts = TrackStats()
    /// Set inside the queue by `finish`. Without it a sample handler already
    /// blocked on `queue.sync` would append after `markAsFinished`, which
    /// raises an Objective-C exception Swift cannot catch.
    private var finished = false

    let url: URL

    init(url: URL, shared: SharedStart) throws {
        self.url = url
        self.shared = shared
        writer = try AVAssetWriter(outputURL: url, fileType: .m4a)
        input = AVAssetWriterInput(
            mediaType: .audio,
            outputSettings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: 48_000,
                AVNumberOfChannelsKey: 2,
                AVEncoderBitRateKey: 96_000,
            ])
        input.expectsMediaDataInRealTime = true
        writer.add(input)
    }

    func append(_ sample: CMSampleBuffer) {
        queue.sync {
            guard !finished else { return }
            guard CMSampleBufferDataIsReady(sample) else {
                counts.dropped += 1
                return
            }

            let pts = CMSampleBufferGetPresentationTimeStamp(sample)
            let origin = sessionStart == nil ? shared.claim(pts) : nil
            let action = sampleAction(pts: pts, sessionStart: sessionStart, sharedOrigin: origin)

            if let at = action.startSessionAt {
                guard writer.startWriting() else {
                    counts.dropped += 1
                    return
                }
                writer.startSession(atSourceTime: at)
                sessionStart = at
            }

            guard action.writeSample else {
                counts.early += 1
                return
            }
            // `expectsMediaDataInRealTime` makes a busy encoder say no, so
            // this branch is normal under load and must be counted, not hidden.
            guard writer.status == .writing, input.isReadyForMoreMediaData else {
                counts.dropped += 1
                return
            }
            input.append(sample)
            counts.appended += 1
        }
    }

    var stats: TrackStats { queue.sync { counts } }

    func finish() {
        let done = DispatchSemaphore(value: 0)
        queue.sync {
            finished = true
            guard sessionStart != nil, writer.status == .writing else {
                done.signal()
                return
            }
            input.markAsFinished()
            writer.finishWriting { done.signal() }
        }
        if done.wait(timeout: .now() + 30) == .timedOut {
            note("warning: \(url.lastPathComponent) did not finish writing within 30s")
            note("warning: \(url.path) has no index yet and may be unplayable")
        }
    }
}

// MARK: - Stream plumbing

final class Sink: NSObject, SCStreamOutput, SCStreamDelegate {
    private let system: TrackWriter
    private let mic: TrackWriter

    init(system: TrackWriter, mic: TrackWriter) {
        self.system = system
        self.mic = mic
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer, of type: SCStreamOutputType) {
        switch type {
        case .audio: system.append(sample)
        case .microphone: mic.append(sample)
        default: break
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        note("stream stopped: \(error.localizedDescription)")
    }
}

// MARK: - Stop on Ctrl-C

final class StopWaiter {
    private var continuation: CheckedContinuation<Void, Never>?
    private var fired = false
    private let lock = NSLock()
    private var sources: [DispatchSourceSignal] = []

    /// True once a signal has asked for the stop. Read by `--self-test`.
    var hasStopped: Bool {
        lock.lock()
        defer { lock.unlock() }
        return fired
    }

    func arm() {
        // SIGHUP belongs here with the other two. macOS sends it when the
        // terminal window closes, and its default action kills the process on
        // the spot. That leaves an .m4a with no index, and nothing can play or
        // transcribe such a file.
        for sig in [SIGINT, SIGTERM, SIGHUP] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
            source.setEventHandler { [weak self] in self?.fire(sig) }
            source.resume()
            sources.append(source)
        }
    }

    private func fire(_ received: Int32) {
        lock.lock()
        let first = !fired
        fired = true
        let waiting = continuation
        continuation = nil
        lock.unlock()

        if first {
            waiting?.resume()
            return
        }

        // Only a second ctrl-c means the user will not wait for the encoder.
        // Leave then, and say what it costs. Without this the only way out of
        // a hung encoder is kill -9.
        //
        // Any other repeat is the same request twice, so it must be ignored.
        // ctrl-c on `qn watch` interrupts the whole process group, and
        // watch_cleanup sends the recorder its own SIGTERM milliseconds later.
        // Acting on that second signal destroyed the recording it was ending.
        guard received == SIGINT else { return }

        FileHandle.standardError.write("\r\u{1B}[K".data(using: .utf8)!)
        note("force-quitting — the recording is unfinished and may be unplayable")
        exit(130)
    }

    func wait() async {
        await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
            lock.lock()
            if fired {
                lock.unlock()
                c.resume()
                return
            }
            continuation = c
            lock.unlock()
        }
    }
}

// MARK: - Helpers

func note(_ message: String) {
    FileHandle.standardError.write("recorder: \(message)\n".data(using: .utf8)!)
}

func fail(_ message: String) -> Never {
    note(message)
    exit(1)
}

// MARK: - Self test

/// Exercises the logic that decides where both tracks begin and what the user
/// is told about lost samples. Everything here is pure: no stream, no writer,
/// no microphone, so it runs on any machine with no permissions.
func runSelfTest() -> Int32 {
    var failures = 0
    func check(_ name: String, _ passed: Bool) {
        failures += passed ? 0 : 1
        print("\(passed ? "ok  " : "FAIL") \(name)")
    }

    func time(_ seconds: Double) -> CMTime { CMTime(seconds: seconds, preferredTimescale: 48_000) }

    // The shared origin.
    var shared = SharedStart()
    check("the first claim keeps its own timestamp", shared.claim(time(10)) == time(10))
    check("a later claim gets the first timestamp", shared.claim(time(11)) == time(10))
    check("an earlier claim also gets the first timestamp", shared.claim(time(9)) == time(10))

    // Both tracks land on one origin.
    shared = SharedStart()
    let systemFirst = sampleAction(pts: time(10), sessionStart: nil, sharedOrigin: shared.claim(time(10)))
    let micFirst = sampleAction(pts: time(10.3), sessionStart: nil, sharedOrigin: shared.claim(time(10.3)))
    check("the track that arrives first opens the session", systemFirst.startSessionAt == time(10))
    check("the track that arrives second opens at the same time", micFirst.startSessionAt == time(10))
    check("the second track still writes its first sample", micFirst.writeSample)

    // A track whose first sample predates the origin.
    shared = SharedStart()
    _ = shared.claim(time(10))
    let stale = sampleAction(pts: time(9.8), sessionStart: nil, sharedOrigin: shared.claim(time(9.8)))
    check("a sample older than the origin still opens the session at the origin",
          stale.startSessionAt == time(10))
    check("a sample older than the origin is not written", !stale.writeSample)

    // Steady state.
    let later = sampleAction(pts: time(12), sessionStart: time(10), sharedOrigin: nil)
    check("a running session does not reopen", later.startSessionAt == nil)
    check("a sample after the session start is written", later.writeSample)

    let backwards = sampleAction(pts: time(9), sessionStart: time(10), sharedOrigin: nil)
    check("a sample before a running session is not written", !backwards.writeSample)

    let broken = sampleAction(pts: .invalid, sessionStart: nil, sharedOrigin: nil)
    check("an unusable timestamp never opens a session", broken.startSessionAt == nil)
    check("an unusable timestamp is not written", !broken.writeSample)

    // Loss reporting.
    check("a clean track reports nothing",
          lossReport(track: "me.m4a", stats: TrackStats(appended: 100, dropped: 0, early: 0)) == nil)
    check("a tiny loss is reported without a warning",
          lossReport(track: "me.m4a", stats: TrackStats(appended: 1000, dropped: 1, early: 0))?
            .hasPrefix("warning:") == false)
    check("a large loss is reported as a warning",
          lossReport(track: "them.m4a", stats: TrackStats(appended: 90, dropped: 10, early: 0))?
            .hasPrefix("warning:") == true)
    check("early samples count as loss too",
          lossReport(track: "me.m4a", stats: TrackStats(appended: 100, dropped: 0, early: 2)) != nil)

    // Signals. Every signal that can reach a recording must end it through
    // the encoder. A signal that kills the process instead costs the whole
    // meeting, because the index is written last.
    //
    // Both checks below are proved by arriving at them at all: an unhandled
    // SIGHUP, or a force-quit on the SIGTERM, would end this process first.
    let stopper = StopWaiter()
    stopper.arm()

    kill(getpid(), SIGHUP)
    var stopped = false
    for _ in 0..<200 {
        stopped = stopper.hasStopped
        if stopped { break }
        usleep(10_000)
    }
    check("a hangup asks for a clean stop", stopped)

    // The duplicate `qn watch` sends: ctrl-c already interrupted the group,
    // and watch_cleanup follows it with a SIGTERM of its own.
    kill(getpid(), SIGTERM)
    usleep(200_000)
    check("a repeated stop signal never force-quits the encoder", true)

    print(failures == 0 ? "all checks passed" : "\(failures) check(s) failed")
    return failures == 0 ? 0 : 1
}

// MARK: - Run

func record(into directory: URL) async throws {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    guard let display = content.displays.first else {
        fail("no display found — ScreenCaptureKit needs one even for audio-only capture")
    }

    let config = SCStreamConfiguration()
    config.capturesAudio = true
    config.excludesCurrentProcessAudio = true
    config.sampleRate = 48_000
    config.channelCount = 2
    config.captureMicrophone = true
    // Video is unused, so ask for the smallest and slowest frames allowed.
    config.width = 2
    config.height = 2
    config.showsCursor = false
    config.minimumFrameInterval = CMTime(value: 1, timescale: 1)

    // One origin for both files, so their timestamps mean the same thing.
    let shared = SharedStart()
    let system = try TrackWriter(url: directory.appendingPathComponent("them.m4a"), shared: shared)
    let mic = try TrackWriter(url: directory.appendingPathComponent("me.m4a"), shared: shared)
    let sink = Sink(system: system, mic: mic)

    let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
    let stream = SCStream(filter: filter, configuration: config, delegate: sink)
    try stream.addStreamOutput(sink, type: .audio, sampleHandlerQueue: DispatchQueue(label: "qn.sys"))
    try stream.addStreamOutput(sink, type: .microphone, sampleHandlerQueue: DispatchQueue(label: "qn.mic"))

    let stopper = StopWaiter()
    stopper.arm()

    try await stream.startCapture()

    let startedAt = Date()
    let ticker = DispatchSource.makeTimerSource(queue: .global())
    ticker.schedule(deadline: .now(), repeating: 1)
    ticker.setEventHandler {
        let elapsed = Int(Date().timeIntervalSince(startedAt))
        let line = String(format: "\r  recording %02d:%02d   ctrl-c to stop", elapsed / 60, elapsed % 60)
        FileHandle.standardError.write(line.data(using: .utf8)!)
    }
    ticker.resume()

    await stopper.wait()

    ticker.cancel()
    FileHandle.standardError.write("\r\u{1B}[K".data(using: .utf8)!)

    try? await stream.stopCapture()
    system.finish()
    mic.finish()

    let systemStats = system.stats
    let micStats = mic.stats

    if let message = lossReport(track: "them.m4a", stats: systemStats) { note(message) }
    if let message = lossReport(track: "me.m4a", stats: micStats) { note(message) }

    if systemStats.appended == 0 {
        note("warning: no system audio captured — check Screen Recording permission")
    }
    if micStats.appended == 0 {
        note("warning: no microphone audio captured — check Microphone permission")
    }
    if systemStats.appended == 0 && micStats.appended == 0 {
        exit(1)
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())

if arguments.first == "--self-test" {
    exit(runSelfTest())
}

guard let first = arguments.first, !first.hasPrefix("--") else {
    fail("usage: recorder <output-dir> | recorder --self-test")
}
let outputDirectory = URL(fileURLWithPath: first, isDirectory: true)

let finished = DispatchSemaphore(value: 0)
var exitCode: Int32 = 0
Task {
    do {
        try await record(into: outputDirectory)
    } catch {
        let reason = error.localizedDescription
        if reason.contains("TCC") || reason.lowercased().contains("declin") {
            note("macOS has not granted Screen Recording permission.")
            note("Open System Settings > Privacy & Security > Screen Recording,")
            note("switch it on for the app you run qn from, then quit and reopen that app.")
        } else {
            note("failed: \(reason)")
        }
        exitCode = 1
    }
    finished.signal()
}
finished.wait()
exit(exitCode)
