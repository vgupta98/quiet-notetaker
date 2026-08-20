// Records a meeting as two separate audio tracks:
//   them.m4a — everything the meeting app plays through your speakers
//   me.m4a   — your microphone
//
// Keeping the tracks apart is what gives the transcript speaker labels
// without any speaker-identification model.
//
// Usage: recorder <output-dir>     Stops on Ctrl-C (SIGINT) or SIGTERM.

import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

// MARK: - One audio track on disk

final class TrackWriter {
    private let writer: AVAssetWriter
    private let input: AVAssetWriterInput
    private let queue = DispatchQueue(label: "qn.track")
    private var started = false
    private var appended = 0

    let url: URL

    init(url: URL) throws {
        self.url = url
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
            guard CMSampleBufferDataIsReady(sample) else { return }
            if !started {
                guard writer.startWriting() else { return }
                writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sample))
                started = true
            }
            guard writer.status == .writing, input.isReadyForMoreMediaData else { return }
            input.append(sample)
            appended += 1
        }
    }

    var sampleCount: Int { queue.sync { appended } }

    func finish() {
        let done = DispatchSemaphore(value: 0)
        queue.sync {
            guard started, writer.status == .writing else {
                done.signal()
                return
            }
            input.markAsFinished()
            writer.finishWriting { done.signal() }
        }
        _ = done.wait(timeout: .now() + 30)
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

    func arm() {
        for sig in [SIGINT, SIGTERM] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .global())
            source.setEventHandler { [weak self] in self?.fire() }
            source.resume()
            sources.append(source)
        }
    }

    private func fire() {
        lock.lock()
        defer { lock.unlock() }
        guard !fired else { return }
        fired = true
        continuation?.resume()
        continuation = nil
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

    let system = try TrackWriter(url: directory.appendingPathComponent("them.m4a"))
    let mic = try TrackWriter(url: directory.appendingPathComponent("me.m4a"))
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

    if system.sampleCount == 0 {
        note("warning: no system audio captured — check Screen Recording permission")
    }
    if mic.sampleCount == 0 {
        note("warning: no microphone audio captured — check Microphone permission")
    }
    if system.sampleCount == 0 && mic.sampleCount == 0 {
        exit(1)
    }
}

guard CommandLine.arguments.count >= 2 else {
    fail("usage: recorder <output-dir>")
}
let outputDirectory = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)

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
