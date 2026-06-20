import AppKit
import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

final class RecorderDelegate: NSObject, SCRecordingOutputDelegate {
    var started = false
    var finished = false
    var failedError: Error?

    func recordingOutputDidStartRecording(_ recordingOutput: SCRecordingOutput) {
        started = true
        fputs("recording started\n", stderr)
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        failedError = error
        finished = true
        fputs("recording failed: \(error)\n", stderr)
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        finished = true
        fputs("recording finished\n", stderr)
    }
}

func argumentValue(_ name: String, default defaultValue: String) -> String {
    let args = CommandLine.arguments
    guard let index = args.firstIndex(of: name), index + 1 < args.count else {
        return defaultValue
    }
    return args[index + 1]
}

func argumentInt(_ name: String, default defaultValue: Int) -> Int {
    Int(argumentValue(name, default: String(defaultValue))) ?? defaultValue
}

let outputPath = argumentValue("--output", default: "/tmp/sodamusic-capture.mp4")
let seconds = argumentInt("--seconds", default: 8)
let appName = argumentValue("--app", default: "汽水音乐")
let outputURL = URL(fileURLWithPath: outputPath)
try? FileManager.default.removeItem(at: outputURL)

let content = try await SCShareableContent.current
guard let display = content.displays.first else {
    fatalError("no capturable display found")
}

let matchingApps = content.applications.filter { app in
    app.applicationName.localizedCaseInsensitiveContains(appName)
        || app.bundleIdentifier.localizedCaseInsensitiveContains("SodaMusic")
}
guard !matchingApps.isEmpty else {
    let names = content.applications.map(\.applicationName).sorted().joined(separator: ", ")
    fatalError("could not find app '\(appName)'. Visible apps: \(names)")
}

let filter = SCContentFilter(
    display: display,
    including: matchingApps,
    exceptingWindows: []
)

let config = SCStreamConfiguration()
config.width = 2
config.height = 2
config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
config.queueDepth = 3
config.showsCursor = false
config.capturesAudio = true
config.excludesCurrentProcessAudio = true
config.sampleRate = 48_000
config.channelCount = 2

let delegate = RecorderDelegate()
let recordingConfig = SCRecordingOutputConfiguration()
recordingConfig.outputURL = outputURL
recordingConfig.outputFileType = .mp4
let recordingOutput = SCRecordingOutput(configuration: recordingConfig, delegate: delegate)
let stream = SCStream(filter: filter, configuration: config, delegate: nil)

try stream.addRecordingOutput(recordingOutput)

try await stream.startCapture()
try await Task.sleep(nanoseconds: UInt64(seconds) * 1_000_000_000)

try await stream.stopCapture()
try? stream.removeRecordingOutput(recordingOutput)

let waitUntil = Date().addingTimeInterval(15)
while !delegate.finished && Date() < waitUntil {
    RunLoop.current.run(mode: .default, before: Date().addingTimeInterval(0.1))
}
if let error = delegate.failedError {
    fatalError("recording failed: \(error)")
}
let attrs = try FileManager.default.attributesOfItem(atPath: outputPath)
let size = attrs[.size] as? NSNumber ?? 0
print(outputPath)
print("bytes=\(size)")
