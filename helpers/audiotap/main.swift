// audiotap — capture du son système, et optionnellement du micro EN MÊME TEMPS
// (Core Audio Process Tap, macOS 14.2+).
//
// Sortie stdout : PCM float32 little-endian à --rate (16 kHz par défaut).
//   sans --with-mic : mono, le son système seul.
//   avec --with-mic : stéréo entrelacé, canal 0 = micro, canal 1 = son système.
//
// Pourquoi --with-mic : sinon le micro (PortAudio, côté Python) et le tap sont deux
// flux à deux horloges, sans horodatage commun. Mesuré sur un vrai appel, les deux
// pistes dérivaient de 0,46 s à 1,48 s l'une par rapport à l'autre — de quoi rendre
// l'anti-écho inopérant et faire compter deux fois chaque phrase de l'interlocuteur.
// Ici, un agrégat privé porte à la fois le périphérique d'entrée et le tap : un seul
// IOProc, une seule horloge, alignement à l'échantillon près par construction.
//
// stderr : lignes "READY ...", "INFO ...", "ERROR ...". Quitte sur SIGTERM/SIGINT,
// quand stdin se ferme (le parent est mort), ou quand un périphérique par défaut
// change (AirPods…) — code 4, le superviseur relance.
//
// Usage : audiotap [--rate 16000] [--with-mic] [--probe]
//   --probe : crée le tap (déclenche la demande d'autorisation « Enregistrement
//             audio système »), écrit READY ou ERROR, et quitte. Code 0 si OK.

import AudioToolbox
import CoreAudio
import Foundation

var outRate: Double = 16000
var probe = false
var withMic = false
var args = CommandLine.arguments.dropFirst().makeIterator()
while let a = args.next() {
    switch a {
    case "--rate": if let v = args.next(), let r = Double(v) { outRate = r }
    case "--probe": probe = true
    case "--with-mic": withMic = true
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

// ---- périphériques par défaut ----
func defaultDevice(_ sel: AudioObjectPropertySelector) -> AudioObjectID {
    var addr = AudioObjectPropertyAddress(
        mSelector: sel,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    var dev = AudioObjectID(kAudioObjectUnknown)
    var size = UInt32(MemoryLayout<AudioObjectID>.size)
    let st = AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &addr, 0, nil, &size, &dev)
    if st != noErr { return AudioObjectID(kAudioObjectUnknown) }
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
    if st != noErr { return "" }
    return uid as String
}
/// Nombre de canaux d'entrée d'un périphérique (0 s'il n'en a pas).
func inputChannels(_ dev: AudioObjectID) -> Int {
    var addr = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyStreamConfiguration,
        mScope: kAudioObjectPropertyScopeInput,
        mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(dev, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: 16)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(dev, &addr, 0, nil, &size, raw) == noErr else { return 0 }
    let abl = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
    return abl.reduce(0) { $0 + Int($1.mNumberChannels) }
}

let outputDev = defaultDevice(kAudioHardwarePropertyDefaultOutputDevice)
if outputDev == kAudioObjectUnknown { fail("aucun périphérique de sortie par défaut") }
let outputUID = deviceUID(outputDev)
if outputUID.isEmpty { fail("UID du périphérique de sortie illisible") }

let inputDev = withMic ? defaultDevice(kAudioHardwarePropertyDefaultInputDevice) : AudioObjectID(kAudioObjectUnknown)
let inputUID = inputDev == kAudioObjectUnknown ? "" : deviceUID(inputDev)
// Un périphérique agrégé ne peut pas contenir deux fois la même sous-unité : quand
// l'entrée et la sortie sont le même appareil (AirPods), il n'y a qu'une entrée à lister.
let sameDevice = !inputUID.isEmpty && inputUID == outputUID
let micChannels = inputDev == kAudioObjectUnknown ? 0 : inputChannels(inputDev)
let wantMic = withMic && !inputUID.isEmpty && micChannels > 0

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
let tapChannels = Int(fmt.mChannelsPerFrame)

// ---- agrégat privé : sortie par défaut (+ micro) + tap ----
var subDevices: [[String: Any]] = [[kAudioSubDeviceUIDKey as String: outputUID]]
if wantMic && !sameDevice {
    // Dérive compensée : le micro et la sortie peuvent avoir des horloges distinctes.
    subDevices.append([
        kAudioSubDeviceUIDKey as String: inputUID,
        kAudioSubDeviceDriftCompensationKey as String: 1,
    ])
}
let aggDesc: [String: Any] = [
    kAudioAggregateDeviceNameKey as String: "LocalFlow Tap",
    kAudioAggregateDeviceUIDKey as String: "com.louqui.localflow.tap." + UUID().uuidString,
    kAudioAggregateDeviceMainSubDeviceKey as String: outputUID,
    kAudioAggregateDeviceIsPrivateKey as String: true,
    kAudioAggregateDeviceIsStackedKey as String: false,
    kAudioAggregateDeviceTapAutoStartKey as String: true,
    kAudioAggregateDeviceSubDeviceListKey as String: subDevices,
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
info("agg input streams=\(countStreams(aggID, kAudioObjectPropertyScopeInput)) output streams=\(countStreams(aggID, kAudioObjectPropertyScopeOutput)) tapCh=\(tapChannels) micCh=\(micChannels) wantMic=\(wantMic) same=\(sameDevice)")

func cleanup() {
    if aggID != kAudioObjectUnknown { AudioHardwareDestroyAggregateDevice(aggID) }
    if tapID != kAudioObjectUnknown { AudioHardwareDestroyProcessTap(tapID) }
}

if probe {
    cleanup()
    FileHandle.standardError.write("READY probe rate=\(inRate) ch=\(tapChannels)\n".data(using: .utf8)!)
    exit(0)
}

// ---- rééchantillonnage linéaire vers outRate ----
// Une seule phase pour les deux voies : elles restent alignées à l'échantillon près.
let ratio = inRate / outRate
var phase: Double = 0
var lastMic: Float = 0
var lastSys: Float = 0
let writeQueue = DispatchQueue(label: "audiotap.write")
let stdoutHandle = FileHandle.standardOutput
let outChannels = wantMic ? 2 : 1

/// Mixe en mono les canaux d'un buffer entrelacé.
@inline(__always)
func monoize(_ buf: AudioBuffer, into dst: inout [Float]) {
    guard let base = buf.mData else { return }
    let ch = max(Int(buf.mNumberChannels), 1)
    let frames = Int(buf.mDataByteSize) / (4 * ch)
    let p = base.assumingMemoryBound(to: Float.self)
    dst.removeAll(keepingCapacity: true)
    dst.reserveCapacity(frames)
    if ch == 1 {
        for i in 0..<frames { dst.append(p[i]) }
    } else {
        for i in 0..<frames {
            var s: Float = 0
            for c in 0..<ch { s += p[i * ch + c] }
            dst.append(s / Float(ch))
        }
    }
}

var cbCount = 0
var micScratch = [Float]()
var sysScratch = [Float]()
var out = [Float]()
var layoutLogged = false
var procID: AudioDeviceIOProcID?
let ioQueue = DispatchQueue(label: "audiotap.io")
st = AudioDeviceCreateIOProcIDWithBlock(&procID, aggID, ioQueue) { _, inInput, _, _, _ in
    let abl = UnsafeMutableAudioBufferListPointer(UnsafeMutablePointer(mutating: inInput))
    guard abl.count > 0 else { return }

    // Disposition de l'agrégat : les sous-périphériques dans l'ordre de la liste
    // (la sortie n'apporte aucun canal d'ENTRÉE), puis les taps. Avec --with-mic on
    // attend donc [micro, tap] ; sans, [tap] seul. On identifie le tap par son nombre
    // de canaux et on prend l'autre buffer comme micro.
    var micIdx = -1
    var sysIdx = -1
    if wantMic && abl.count >= 2 {
        sysIdx = abl.count - 1              // le tap est toujours listé en dernier
        micIdx = 0
    } else {
        sysIdx = abl.count - 1
    }
    if !layoutLogged {
        layoutLogged = true
        var desc = ""
        for (i, b) in abl.enumerated() { desc += " buf[\(i)]=\(b.mNumberChannels)ch/\(b.mDataByteSize)o" }
        info("layout:\(desc) → micIdx=\(micIdx) sysIdx=\(sysIdx)")
        if wantMic && abl.count < 2 {
            // Le micro n'est pas dans l'agrégat. On le signale — le parent ouvrira le
            // sien — mais on CONTINUE d'écrire deux canaux (micro muet) : changer le
            // cadrage en cours de route décalerait tout ce que le parent lit ensuite.
            FileHandle.standardError.write("ERROR mic-missing\n".data(using: .utf8)!)
        }
    }

    monoize(abl[sysIdx], into: &sysScratch)
    if micIdx >= 0 {
        monoize(abl[micIdx], into: &micScratch)
    }
    let frames = micIdx >= 0 ? min(sysScratch.count, micScratch.count) : sysScratch.count
    if frames == 0 { return }
    cbCount += 1

    out.removeAll(keepingCapacity: true)
    out.reserveCapacity(Int(Double(frames) / ratio + 2) * outChannels)
    var pos = phase
    while pos < Double(frames) {
        let i = Int(pos)
        let frac = Float(pos - Double(i))
        if outChannels == 2 {
            if micIdx >= 0 {
                let a = i == 0 ? lastMic : micScratch[i - 1]
                out.append(a + (micScratch[i] - a) * frac)
            } else {
                out.append(0)   // micro absent : canal muet, cadrage préservé
            }
        }
        let b = i == 0 ? lastSys : sysScratch[i - 1]
        out.append(b + (sysScratch[i] - b) * frac)
        pos += ratio
    }
    phase = pos - Double(frames)
    if micIdx >= 0 { lastMic = micScratch[frames - 1] }
    lastSys = sysScratch[frames - 1]

    let data = out.withUnsafeBufferPointer { Data(buffer: $0) }
    writeQueue.async {
        do { try stdoutHandle.write(contentsOf: data) } catch { exit(0) }
    }
}
if st != noErr { fail("io proc: \(st)") }
st = AudioDeviceStart(aggID, procID)
if st != noErr { fail("start: \(st)") }

FileHandle.standardError.write(
    "READY rate=\(inRate) ch=\(tapChannels) out=\(Int(outRate)) outch=\(outChannels) device=\(outputUID) mic=\(wantMic ? inputUID : "-")\n"
        .data(using: .utf8)!)

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
// changement d'un périphérique par défaut (AirPods…) → on quitte, le parent relance
func watchDefault(_ sel: AudioObjectPropertySelector, _ what: String) {
    var addr = AudioObjectPropertyAddress(
        mSelector: sel,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain)
    AudioObjectAddPropertyListenerBlock(AudioObjectID(kAudioObjectSystemObject), &addr, sigQueue) { _, _ in
        info("default \(what) changed, exiting for restart")
        AudioDeviceStop(aggID, procID); cleanup(); exit(4)
    }
}
watchDefault(kAudioHardwarePropertyDefaultOutputDevice, "output")
if wantMic { watchDefault(kAudioHardwarePropertyDefaultInputDevice, "input") }
_ = srcs
RunLoop.main.run()
