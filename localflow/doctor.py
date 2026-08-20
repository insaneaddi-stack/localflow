"""Vérifications utilisées par l'assistant d'installation.

À lancer AVEC le binaire du bundle (même identité que l'app pour macOS) :
    LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.doctor accessibility   → 0 si OK
    LocalFlow.app/Contents/MacOS/LocalFlow -m localflow.doctor microphone      → 0 si OK
"""

import sys

def accessibility_ok() -> bool:
    try:
        import Quartz
        mask = 1 << Quartz.kCGEventFlagsChanged
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap, Quartz.kCGEventTapOptionListenOnly,
            mask, lambda proxy, t, e, r: e, None,
        )
        return tap is not None
    except Exception:
        return False

def microphone_ok() -> bool:
    try:
        import AVFoundation as AV
        return AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeAudio) == 3
    except Exception:
        return False

def main():
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    ok = {"accessibility": accessibility_ok, "microphone": microphone_ok}.get(what, lambda: False)()
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
