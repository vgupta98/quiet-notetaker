// Detects meetings starting and stopping, and prints one event per line.
//
//   START<TAB>app=zoom.us<TAB>window=Zoom Meeting<TAB>title=SDK Sync<TAB>attendees=Priya, Arjun
//   STOP
//
// The microphone is the trigger and an on-screen meeting window is the
// confirmation. The microphone alone is not enough: Voice Memos, Siri and
// QuickTime all open it, and none of them is a meeting.
//
// The calendar only supplies the title and the attendees. A denied calendar
// permission degrades the event, it never stops detection.
//
// Usage: watcher [--ignore-while <path>] [--self-test]

import CoreAudio
import CoreGraphics
import EventKit
import Foundation

// MARK: - Pure model

/// The on-screen window that made us call this a meeting.
struct MeetingWindow: Equatable {
    let owner: String
    let name: String
}

enum MeetingEvent: Equatable {
    case start(MeetingWindow)
    case stop
}

/// True when this window belongs to a meeting that is in progress.
///
/// Every rule needs the window name, not only the owner. A running Zoom or
/// Teams app with no call open shows its own name in the title bar, and we
/// must not record that.
func classifyWindow(owner: String, name: String) -> Bool {
    let owner = owner.trimmingCharacters(in: .whitespacesAndNewlines)
    let name = name.trimmingCharacters(in: .whitespacesAndNewlines)

    func ownerHas(_ needle: String) -> Bool { owner.range(of: needle, options: .caseInsensitive) != nil }
    func nameHas(_ needle: String) -> Bool { name.range(of: needle, options: .caseInsensitive) != nil }

    if ownerHas("zoom") && nameHas("Zoom Meeting") { return true }

    // Google Meet titles its tab "Meet - <name>", with either dash, in any
    // browser. The owner is the browser, so the name is the only signal.
    // Anchored at the start, because anywhere in the title also matched a
    // document called "Sprint Meet - agenda" in any app at all.
    if name.hasPrefix("Meet – ") || name.hasPrefix("Meet - ") { return true }

    if ownerHas("Slack") && nameHas("Huddle") { return true }

    // Teams and Webex both name the window after the call. A window that
    // carries only the name of the app is an app that is open, not a call.
    if ownerHas("Microsoft Teams") || ownerHas("Webex") {
        return !name.isEmpty && name.compare(owner, options: .caseInsensitive) != .orderedSame
    }

    return false
}

/// Decides when a meeting starts and stops. Pure on purpose: no timers, no
/// Core Audio, no clock of its own, so `--self-test` can drive it.
///
/// `ignoreActive` is our own recorder holding the microphone. It blocks a
/// start but never a stop, otherwise we would record ourselves recording.
struct MeetingStateMachine {
    /// A meeting app drops the microphone for a moment when the audio device
    /// changes: 0.21s at worst over nine measured switches. Leaving one call
    /// and joining the next took 8.44s at its fastest over five. This sits
    /// between them. At 20s it swallowed every handover, so back-to-back
    /// meetings became one recording.
    static let stopDebounce: TimeInterval = 5

    /// A meeting still running after this long is a recorder nobody stopped.
    /// Ending it writes up what there is and frees the next one to start.
    static let maxMeeting: TimeInterval = 4 * 60 * 60

    private(set) var inMeeting = false
    private var quietSince: Date?
    private var startedAt: Date?
    private var stoppedByHand = false

    private mutating func finish() -> MeetingEvent {
        inMeeting = false
        quietSince = nil
        startedAt = nil
        return .stop
    }

    mutating func update(
        micActive: Bool,
        meetingWindow: MeetingWindow?,
        ignoreActive: Bool,
        stopRequested: Bool,
        now: Date
    ) -> MeetingEvent? {
        guard inMeeting else {
            // `qn stop` means stop, so nothing starts again until the call it
            // ended is over. Without this the next tick sees the same live
            // microphone and the same window, and records straight over it.
            if !micActive { stoppedByHand = false }
            guard !stoppedByHand, micActive, !ignoreActive,
                  let window = meetingWindow else { return nil }
            inMeeting = true
            quietSince = nil
            startedAt = now
            return .start(window)
        }

        if stopRequested {
            stoppedByHand = true
            return finish()
        }

        // The cap does not set `stoppedByHand`: a meeting still running after
        // four hours should be cut and carried on, not abandoned.
        if let startedAt, now.timeIntervalSince(startedAt) >= Self.maxMeeting { return finish() }

        // Inside a meeting the window may be minimised or renamed, so only the
        // microphone decides the end.
        if micActive {
            quietSince = nil
            return nil
        }

        guard let since = quietSince else {
            quietSince = now
            return nil
        }
        guard now.timeIntervalSince(since) >= Self.stopDebounce else { return nil }

        return finish()
    }
}

// MARK: - Event output

/// Tabs separate the fields and newlines separate the events, so no value may
/// contain either one.
func sanitize(_ value: String) -> String {
    var out = ""
    for character in value {
        out.append(character.isNewline || character == "\t" ? " " : character)
    }
    return out.trimmingCharacters(in: .whitespaces)
}

func emit(_ line: String) {
    // The reader is a pipe, and a pipe makes stdout block-buffered. Write the
    // bytes ourselves so every event leaves the process the moment it happens.
    FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!)
}

func startLine(window: MeetingWindow, title: String?, attendees: [String]) -> String {
    var fields = ["START", "app=" + sanitize(window.owner), "window=" + sanitize(window.name)]
    if let title, !title.isEmpty {
        fields.append("title=" + sanitize(title))
    }
    if !attendees.isEmpty {
        fields.append("attendees=" + sanitize(attendees.joined(separator: ", ")))
    }
    return fields.joined(separator: "\t")
}

func note(_ message: String) {
    FileHandle.standardError.write("watcher: \(message)\n".data(using: .utf8)!)
}

// MARK: - Microphone

/// Reports whether anything except our own recorder is on the microphone.
///
/// Asking the device instead — `kAudioDevicePropertyDeviceIsRunningSomewhere`
/// — cannot answer that, because our recorder is on the microphone too. The
/// flag stayed true for the whole meeting, the quiet the stop waits for never
/// came, and on the built-in microphone a call never ended at all. Bluetooth
/// earbuds hid it: they drop their link when the meeting app lets go.
///
/// So the question is per process. A device listener still wakes the poll
/// early, and re-attaches when macOS moves to another input device.
final class MicMonitor {
    private let lock = NSLock()
    private var device = AudioObjectID(kAudioObjectUnknown)
    private var listener: AudioObjectPropertyListenerBlock?
    private let onChange: () -> Void

    private static var runningAddress = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceIsRunningSomewhere,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)

    /// ScreenCaptureKit takes the microphone through this daemon, so our own
    /// recorder appears under Apple's name here and not ours. Measured with the
    /// recorder running and no meeting app open: the only process on the input
    /// was com.apple.replayd. Any other ScreenCaptureKit capture is hidden with
    /// it, which is right: a screen recording is not a meeting.
    private static let ourCapture = "com.apple.replayd"

    init(onChange: @escaping () -> Void) {
        self.onChange = onChange
    }

    /// True while some process other than our own capture holds the microphone.
    func isActive() -> Bool {
        followDefaultDevice()
        return Self.audioProcesses().contains { process in
            Self.isOnInput(process) && Self.bundleID(process) != Self.ourCapture
        }
    }

    /// Keeps the wake-up listener on whichever device macOS now calls default.
    private func followDefaultDevice() {
        let current = Self.defaultInputDevice()
        lock.lock()
        defer { lock.unlock() }
        guard current != device else { return }
        removeListener()
        device = current
        addListener()
    }

    /// Every process Core Audio knows about. Empty when it will not say.
    private static func audioProcesses() -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyProcessObjectList,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        let system = AudioObjectID(kAudioObjectSystemObject)
        var size = UInt32(0)
        guard AudioObjectGetPropertyDataSize(system, &address, 0, nil, &size) == noErr else {
            return []
        }
        var found = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(system, &address, 0, nil, &size, &found) == noErr else {
            return []
        }
        return found
    }

    private static func isOnInput(_ process: AudioObjectID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyIsRunningInput,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var running = UInt32(0)
        var size = UInt32(MemoryLayout<UInt32>.size)
        let status = AudioObjectGetPropertyData(process, &address, 0, nil, &size, &running)
        return status == noErr && running != 0
    }

    private static func bundleID(_ process: AudioObjectID) -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyBundleID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var name: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        let status = withUnsafeMutablePointer(to: &name) {
            AudioObjectGetPropertyData(process, &address, 0, nil, &size, $0)
        }
        return status == noErr ? (name as String) : ""
    }

    private func addListener() {
        guard device != AudioObjectID(kAudioObjectUnknown) else { return }
        let block: AudioObjectPropertyListenerBlock = { [weak self] _, _ in self?.onChange() }
        let status = AudioObjectAddPropertyListenerBlock(device, &Self.runningAddress, .main, block)
        if status == noErr {
            listener = block
        } else {
            note("cannot observe the microphone (\(status)); falling back to polling")
        }
    }

    private func removeListener() {
        guard let listener, device != AudioObjectID(kAudioObjectUnknown) else { return }
        AudioObjectRemovePropertyListenerBlock(device, &Self.runningAddress, .main, listener)
        self.listener = nil
    }

    private static func defaultInputDevice() -> AudioObjectID {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultInputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain)
        var device = AudioObjectID(kAudioObjectUnknown)
        var size = UInt32(MemoryLayout<AudioObjectID>.size)
        let status = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &device)
        return status == noErr ? device : AudioObjectID(kAudioObjectUnknown)
    }
}

// MARK: - Windows

/// The first on-screen window that looks like a live meeting.
func currentMeetingWindow() -> MeetingWindow? {
    guard let windows = CGWindowListCopyWindowInfo(.optionOnScreenOnly, kCGNullWindowID) as? [[String: Any]] else {
        return nil
    }
    for window in windows {
        let owner = window[kCGWindowOwnerName as String] as? String ?? ""
        // Without Screen Recording permission macOS hides every window name,
        // and then no rule can match. `qn doctor` is the place that reports it.
        let name = window[kCGWindowName as String] as? String ?? ""
        if classifyWindow(owner: owner, name: name) {
            return MeetingWindow(owner: owner, name: name)
        }
    }
    return nil
}

// MARK: - Calendar

/// Names the meeting from the calendar. Optional enrichment: any failure here
/// returns nothing and the START event simply carries fewer fields.
final class CalendarLookup {
    private let store = EKEventStore()
    private var authorized = false
    private var complained = false

    /// Joining early and joining late are both normal, so the calendar entry
    /// is allowed to be a little out of step with the microphone.
    private let earlyTolerance: TimeInterval = 5 * 60
    private let lateTolerance: TimeInterval = 15 * 60

    init() {
        requestAccess()
    }

    private func requestAccess() {
        let done = DispatchSemaphore(value: 0)
        store.requestFullAccessToEvents { [weak self] granted, error in
            self?.authorized = granted
            if !granted {
                self?.complain(error?.localizedDescription ?? "permission denied")
            }
            done.signal()
        }
        _ = done.wait(timeout: .now() + 10)
    }

    private func complain(_ reason: String) {
        guard !complained else { return }
        complained = true
        note("no calendar access (\(reason)); events will have no title or attendees")
    }

    /// Returns the title and the attendee names of the event covering `now`.
    func describe(at now: Date) -> (title: String, attendees: [String])? {
        guard authorized else { return nil }

        let predicate = store.predicateForEvents(
            withStart: now.addingTimeInterval(-4 * 60 * 60),
            end: now.addingTimeInterval(4 * 60 * 60),
            calendars: nil)

        let candidates = store.events(matching: predicate)
            .filter { !$0.isAllDay && $0.status != .canceled }
            .filter { event in
                guard let start = event.startDate, let end = event.endDate else { return false }
                return now >= start - earlyTolerance && now <= end + lateTolerance
            }

        // A day holds overlapping entries, so prefer the one that started last
        // before now: that is the call the user just joined.
        guard let event = candidates.max(by: { ($0.startDate ?? .distantPast) < ($1.startDate ?? .distantPast) }) else {
            return nil
        }

        let attendees = (event.attendees ?? [])
            .filter { !$0.isCurrentUser }
            .compactMap { $0.name }
            .filter { !$0.isEmpty }
        return (event.title ?? "", attendees)
    }
}

// MARK: - Self-test

/// Exercises the two pure parts. Everything else needs a microphone, a screen
/// and a calendar, so it stays out of here.
func runSelfTest() -> Int32 {
    var failures = 0
    func check(_ name: String, _ passed: Bool) {
        failures += passed ? 0 : 1
        emit("\(passed ? "ok  " : "FAIL") \(name)")
    }

    let zoom = MeetingWindow(owner: "zoom.us", name: "Zoom Meeting")
    let t0 = Date(timeIntervalSince1970: 1_000_000)
    func at(_ seconds: TimeInterval) -> Date { t0.addingTimeInterval(seconds) }

    // Starting.
    var machine = MeetingStateMachine()
    check("mic on plus zoom window starts a meeting",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0) == .start(zoom))

    machine = MeetingStateMachine()
    check("mic on with no meeting window stays quiet",
          machine.update(micActive: true, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: t0) == nil)

    machine = MeetingStateMachine()
    let memos = MeetingWindow(owner: "Voice Memos", name: "Voice Memos")
    check("mic on with Voice Memos stays quiet",
          machine.update(micActive: true,
                         meetingWindow: classifyWindow(owner: memos.owner, name: memos.name) ? memos : nil,
                         ignoreActive: false, stopRequested: false, now: t0) == nil)

    machine = MeetingStateMachine()
    check("the ignore file blocks the start",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: true, stopRequested: false, now: t0) == nil)

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    check("a second start needs an intervening stop",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: at(5)) == nil)

    // Stopping.
    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: t0)
    check("two quiet seconds do not stop the meeting",
          machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(2)) == nil)
    check("six quiet seconds stop the meeting",
          machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(6)) == .stop)
    check("the meeting is over after the stop", machine.inMeeting == false)

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: false, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: at(3))
    check("the microphone coming back cancels the stop",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: at(20)) == nil)
    check("the meeting survives the audio device switch", machine.inMeeting == true)

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(6))
    check("a stop is emitted once",
          machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(60)) == nil)

    // The cap, which is the only stop a live microphone cannot postpone.
    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    check("a live microphone does not keep a meeting past four hours",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false,
                         now: at(4 * 60 * 60)) == .stop)
    check("the meeting is over after the cap", machine.inMeeting == false)

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    check("a meeting one second under the cap is left alone",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false,
                         now: at(4 * 60 * 60 - 1)) == nil)

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: t0)
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(6))
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false, stopRequested: false, now: at(12))
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false, now: at(13))
    check("the cap is measured from this meeting, not the one before it",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false, stopRequested: false,
                         now: at(4 * 60 * 60)) == nil)

    // `qn stop`.
    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                       stopRequested: false, now: t0)
    check("a stop request ends the meeting at once",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: true, now: at(5)) == .stop)
    check("the meeting is over after a stop request", machine.inMeeting == false)
    check("a live microphone does not start it again",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: false, now: at(7)) == nil)
    check("still nothing minutes later",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: false, now: at(600)) == nil)
    _ = machine.update(micActive: false, meetingWindow: nil, ignoreActive: false,
                       stopRequested: false, now: at(610))
    check("the next call starts once the microphone has been quiet",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: false, now: at(620)) == .start(zoom))

    machine = MeetingStateMachine()
    check("a stop request outside a meeting starts nothing and stops nothing",
          machine.update(micActive: false, meetingWindow: nil, ignoreActive: false,
                         stopRequested: true, now: t0) == nil)
    check("and the next meeting is unaffected",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: false, now: at(5)) == .start(zoom))

    machine = MeetingStateMachine()
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                       stopRequested: false, now: t0)
    _ = machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                       stopRequested: false, now: at(4 * 60 * 60))
    check("the four-hour cap carries on recording, unlike a stop request",
          machine.update(micActive: true, meetingWindow: zoom, ignoreActive: false,
                         stopRequested: false, now: at(4 * 60 * 60 + 2)) == .start(zoom))

    // Window classification.
    let positives: [(String, String)] = [
        ("zoom.us", "Zoom Meeting"),
        ("Google Chrome", "Meet - SDK Sync"),
        ("Safari", "Meet – SDK Sync"),
        ("Microsoft Teams", "Weekly sync | Microsoft Teams"),
        ("Slack", "Huddle in #sdk-team"),
        ("Webex", "Cisco Webex Meeting"),
    ]
    for (owner, name) in positives {
        check("meeting window: \(owner) / \(name)", classifyWindow(owner: owner, name: name))
    }

    let negatives: [(String, String)] = [
        ("Finder", "Downloads"),
        ("Safari", "Apple"),
        ("Voice Memos", "Voice Memos"),
        ("zoom.us", "Zoom"),
        ("Microsoft Teams", "Microsoft Teams"),
        ("Microsoft Teams", ""),
        ("Webex", "Webex"),
        ("Webex", ""),
        ("Slack", "RudderStack"),
        ("QuickTime Player", "Audio Recording"),
        ("Notes", "Sprint Meet - agenda"),
    ]
    for (owner, name) in negatives {
        check("not a meeting window: \(owner) / \(name)", !classifyWindow(owner: owner, name: name))
    }

    // Event formatting.
    check("a start line carries the calendar fields",
          startLine(window: zoom, title: "SDK Sync", attendees: ["Priya", "Arjun"])
            == "START\tapp=zoom.us\twindow=Zoom Meeting\ttitle=SDK Sync\tattendees=Priya, Arjun")
    check("a start line without a calendar match drops those fields",
          startLine(window: zoom, title: nil, attendees: [])
            == "START\tapp=zoom.us\twindow=Zoom Meeting")
    check("tabs and newlines never reach the output",
          startLine(window: MeetingWindow(owner: "zoom.us", name: "Zoom\tMeeting"), title: "a\nb", attendees: [])
            == "START\tapp=zoom.us\twindow=Zoom Meeting\ttitle=a b")

    emit(failures == 0 ? "all checks passed" : "\(failures) check(s) failed")
    return failures == 0 ? 0 : 1
}

// MARK: - Run

/// Joins the microphone, the window list and the calendar to the state machine.
final class Watcher {
    private let ignoreWhile: String?
    private let stopWhen: String?
    private let calendar = CalendarLookup()
    private let queue = DispatchQueue(label: "qn.watcher")
    private var machine = MeetingStateMachine()
    private var monitor: MicMonitor!
    private var timer: DispatchSourceTimer?

    init(ignoreWhile: String?, stopWhen: String?) {
        self.ignoreWhile = ignoreWhile
        self.stopWhen = stopWhen
        // The listener fires on any thread, so it hands the work to the same
        // serial queue as the timer and the state machine stays single-owner.
        monitor = MicMonitor { [unowned self] in self.queue.async { self.tick() } }
    }

    func run() -> Never {
        // The listener answers within milliseconds. The timer covers the case
        // where macOS moves to another input device and the listener on the old
        // one goes quiet.
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: 2)
        timer.setEventHandler { [unowned self] in self.tick() }
        timer.resume()
        self.timer = timer
        dispatchMain()
    }

    private func tick() {
        let now = Date()
        let micActive = monitor.isActive()
        // The window list is the expensive call, so only ask for it when the
        // microphone says something might be happening.
        let window = micActive ? currentMeetingWindow() : nil
        let ignoreActive = ignoreWhile.map { FileManager.default.fileExists(atPath: $0) } ?? false
        // Taken away as it is read. A request that arrives between meetings has
        // nothing to stop, and must not stop the next one instead.
        var stopRequested = false
        if let stopWhen, FileManager.default.fileExists(atPath: stopWhen) {
            stopRequested = true
            try? FileManager.default.removeItem(atPath: stopWhen)
        }

        let event = machine.update(
            micActive: micActive, meetingWindow: window, ignoreActive: ignoreActive,
            stopRequested: stopRequested, now: now)

        switch event {
        case .start(let window):
            let match = calendar.describe(at: now)
            emit(startLine(window: window, title: match?.title, attendees: match?.attendees ?? []))
        case .stop:
            emit("STOP")
        case nil:
            break
        }
    }
}

var ignoreWhile: String?
var stopWhen: String?
var selfTest = false
var arguments = Array(CommandLine.arguments.dropFirst())
while let argument = arguments.first {
    arguments.removeFirst()
    switch argument {
    case "--self-test":
        selfTest = true
    case "--ignore-while":
        guard let path = arguments.first else {
            note("--ignore-while needs a path")
            exit(2)
        }
        arguments.removeFirst()
        ignoreWhile = path
    case "--stop-when":
        guard let path = arguments.first else {
            note("--stop-when needs a path")
            exit(2)
        }
        arguments.removeFirst()
        stopWhen = path
    default:
        note("unknown argument: \(argument)")
        note("usage: watcher [--ignore-while <path>] [--stop-when <path>] [--self-test]")
        exit(2)
    }
}

if selfTest {
    exit(runSelfTest())
}

Watcher(ignoreWhile: ignoreWhile, stopWhen: stopWhen).run()
