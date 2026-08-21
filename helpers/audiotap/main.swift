// audiotap — capture du son système (Core Audio Process Tap, macOS 14.2+)
// Sortie : PCM float32 little-endian, mono, 16 kHz (ou --rate N) sur stdout.
// stderr : lignes "READY ...", "INFO ...", "ERROR ...". Quitte sur SIGTERM/SIGINT
// ou quand stdin se ferme (le parent est mort).
//
// Usage : audiotap [--rate 16000] [--probe]
//   --probe : crée le tap (déclenche la demande d'autorisation « Enregistrement
//             audio système »), écrit READY ou ERROR, et quitte. Code 0 si OK.

import AudioToolbox
import CoreAudio
import Foundation

var outRate: Double = 16000
var probe = false
var args = CommandLine.arguments.dropFirst().makeIterator()
while let a = args.next() {
    switch a {
    case "--rate": if let v = args.next(), let r = Double(v) { outRate = r }
    case "--probe": probe = true
    default: break
    }
}

func fail(_ msg: String, code: Int32 = 2) -> Never {
    FileHandle.standardError.write(("ERROR " + msg + "\n").data(using: .utf8)!)
    exit(code)
}
func info(_ msg: String) {
    FileHandle.standardError.write(("INFO " + msg + "\n").data(using: .utf8)!)
}

// ---- périphérique de sortie par défaut ----
func defaultOutputDevice() -> AudioObjectID {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDefaultOutputDevice,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var dev = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &dev)
    if st != noErr { fail("default output device: \(st)") }
    return dev
}
func deviceUID(_ dev: AudioObjectID) -> String {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceUID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var uid: CFString = "" as CFString
    var size = UInt32(MemoryLayout<CFString>.size)
    let st = withUnsafeMutablePointer(to: &uid) { p in
        AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, p)
    }
    if st != noErr { fail("device uid: \(st)") }
    return uid as String
}

let outputDev = defaultOutputDevice()
let outputUID = deviceUID(outputDev)

// ---- tap : tout le système (mix stéréo), sans couper le son ----
let tapDesc = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
tapDesc.uuid = UUID()
tapDesc.muteBehavior = .unmuted
tapDesc.name = "LocalFlow"
var tapID = AudioObjectID(kAudioObjectUnknown)
var st = AudioHardwareCreateProcessTap(tapDesc, &tapID)
if st != noErr || tapID == kAudioObjectUnknown {
    fail("tap refusé (autorisation « Enregistrement audio système » ?) code=\(st)", code: 3)
}

// Format du tap
var fmt = AudioStreamBasicDescription()
do {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioTapPropertyFormat,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
    st = AudioObjectGetPropertyData(tapID, &addr, 0, nil, &size, &fmt)
    if st != noErr { fail("tap format: \(st)") }
}
let inRate = fmt.mSampleRate
let inCh = Int(fmt.mChannelsPerFrame)
let interleaved = (fmt.mFormatFlags & kAudioFormatFlagIsNonInterleaved) == 0

// ---- agrégat privé : sortie par défaut + tap ----
let aggDesc: [String: Any] = [
    kAudioAggregateDeviceNameKey as String: "LocalFlow Tap",
    kAudioAggregateDeviceUIDKey as String: "com.louqui.localflow.tap." + UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey as String: outputUID,
    kAudioAggregateDeviceIsPrivateKey as String: true,
    kAudioAggregateDeviceIsStackedKey as String: false,
    kAudioAggregateDeviceTapAutoStartKey as String: true,
    kAudioAggregateDeviceSubDeviceListKey as String: [[kAudioSubDeviceUIDKey as String: outputUID]],
    kAudioAggregateDeviceTapListKey as String: [[
        kAudioSubTapDriftCompensationKey as String: true,
        kAudioSubTapUIDKey as String: tapDesc.uuid.uuidString,
    ]],
]
var aggID = AudioObjectID(kAudioObjectUnknown)
st = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &aggID)
if st != noErr { fail("aggregate device: \(st)") }


func countStreams(_ dev: AudioObjectID, _ scope: AudioObjectPropertyScope) -> Int {
    var addr = AudioObjectPropertyAddress(mSelector: kAudioDevicePropertyStreams, mScope: scope, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    if AudioObjectGetPropertyDataSize(dev, &addr, 0, nil, &size) != noErr { return -1 }
    return Int(size) / MemoryLayout<AudioObjectID>.size
}
info("agg input streams=\(countStreams(aggID, kAudioObjectPropertyScopeInput)) output streams=\(countStreams(aggID, kAudioObjectPropertyScopeOutput)) fmt=\(fmt.mFormatID) flags=\(fmt.mFormatFlags) bits=\(fmt.mBitsPerChannel)")

func cleanup() {
    if aggID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggID) }
    if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
}

if probe {
    cleanup()
    FileHandle.standardError.write("READY probe rate=\(inRate) ch=\(inCh)\n".data(using: .utf8)!)
    exit(0)
}

// ---- rééchantillonnage linéaire vers outRate, mono ----
let ratio = inRate / outRate
var phase: Double = 0
var lastSample: Float = 0
let writeQueue = DispatchQueue(label: "audiotap.write")
let stdoutHandle = FileHandle.standardOutput

var cbCount = 0
var procID: AudioDeviceIOProcID?
let ioQueue = DispatchQueue(label: "audiotap.io")
st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, ioQueue) { _, inInput, _, _, _ in
    let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inInput))
    guard abl.count > 0, let base = abl[0].mData else { return }
    let frames: Int
    var mono = [Float]()
    if interleaved {
        let ch = Int(abl[0].mNumberChannels)
        frames = Int(abl[0].mDataByteSize) / (4 * max(ch, 1))
        let p = base.assumingMemoryBound(to: Float.self)
        mono.reserveCapacity(frames)
        for i in 0..<frames {
            var s: Float = 0
            for c in 0..<ch { s += p[i * ch + c] }
            mono.append(s / Float(max(ch, 1)))
        }
    } else {
        frames = Int(abl[0].mDataByteSize) / 4
        mono = [Float](repeating: 0, count: frames)
        let nb = abl.count
        for b in 0..<nb {
            guard let d = abl[b].mData else { continue }
            let p = d.assumingMemoryBound(to: Float.self)
            for i in 0..<frames { mono[i] += p[i] }
        }
        if nb > 1 { for i in 0..<frames { mono[i] /= Float(nb) } }
    }
    cbCount += 1; if cbCount == 1 { info("first callback frames=\(frames) bufs=\(abl.count) ch=\(abl[0].mNumberChannels) bytes=\(abl[0].mDataByteSize)") }
    if frames == 0 { return }
    // interpolation linéaire (ratio typique 3.0 pour 48 kHz → 16 kHz)
    var out = [Float]()
    out.reserveCapacity(Int(Double(frames) / ratio) + 2)
    var pos = phase
    while pos < Double(frames) {
        let i = Int(pos)
        let frac = Float(pos - Double(i))
        let a = i == 0 ? lastSample : mono[i - 1]
        // a = échantillon précédent, b = courant : on interpole entre (i-1) et i
        let b = mono[i]
        out.append(a + (b - a) * frac)
        pos += ratio
    }
    phase = pos - Double(frames)
    lastSample = mono[frames - 1]
    let data = out.withUnsafeBufferPointer { Data(buffer: $0) }
    writeQueue.async {
        do { try stdoutHandle.write(contentsOf: data) } catch { exit(0) }
    }
}
if st != noErr { fail("io proc: \(st)") }
st = AudioDeviceStart(aggID, procID)
if st != noErr { fail("start: \(st)") }

FileHandle.standardError.write("READY rate=\(inRate) ch=\(inCh) out=\(Int(outRate)) device=\(outputUID)\n".data(using: .utf8)!)

// ---- arrêt propre ----
signal(SIGINT, SIG_IGN); signal(SIGTERM, SIG_IGN); signal(SIGPIPE, SIG_IGN)
let sigQueue = DispatchQueue(label: "audiotap.sig")
let srcs = [SIGINT, SIGTERM].map { s -> DispatchSourceSignal in
    let src = DispatchSource.makeSignalSource(signal: s, queue: sigQueue)
    src.setEventHandler { AudioDeviceStop(aggID, procID); cleanup(); exit(0) }
    src.resume()
    return src
}
// parent mort → stdin fermé
DispatchQueue.global().async {
    _ = FileHandle.standardInput.readDataToEndOfFile()
    info("stdin closed after \(cbCount) callbacks")
    AudioDeviceStop(aggID, procID); cleanup(); exit(0)
}
// changement de sortie par défaut (AirPods…) → on quitte, le parent relance
var addrOut = AudioObjectPropertyAddress(
    mSelector: kAudioHardwarePropertyDefaultOutputDevice,
    mScope: kAudioObjectPropertyScopeGlobal,
    mElement: kAudioObjectPropertyElementMain)
AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addrOut, sigQueue) { _, _ in
    info("default output changed, exiting for restart")
    AudioDeviceStop(aggID, procID); cleanup(); exit(4)
}
_ = srcs
RunLoop.main.run()
