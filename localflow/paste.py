"""Collage du texte dans l'app active : presse-papiers + Cmd+V synthétique."""

import threading
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

V_KEYCODE = 9  # touche 'v' (même position physique en AZERTY)
RESTORE_DELAY_S = 0.6

def _snapshot_clipboard():
    """Copie tous les items et tous les types (texte, images, RTF…)."""
    pb = NSPasteboard.generalPasteboard()
    items = []
    for item in pb.pasteboardItems() or []:
        entry = []
        for t in item.types():
            data = item.dataForType_(t)
            if data is not None:
                entry.append((t, data))
        if entry:
            items.append(entry)
    return items

def _restore_clipboard(items):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    new_items = []
    for entry in items:
        it = NSPasteboardItem.alloc().init()
        for t, data in entry:
            it.setData_forType_(data, t)
        new_items.append(it)
    if new_items:
        pb.writeObjects_(new_items)

def _set_clipboard(text):
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setString_forType_(text, NSPasteboardTypeString)

def _press_cmd_v():
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    down = Quartz.CGEventCreateKeyboardEvent(source, V_KEYCODE, True)
    up = Quartz.CGEventCreateKeyboardEvent(source, V_KEYCODE, False)
    Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)

def _press_key(keycode, flags=0):
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for is_down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(source, keycode, is_down)
        Quartz.CGEventSetFlags(e, flags)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)

def press_undo():
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for is_down in (True, False):
        e = Quartz.CGEventCreateKeyboardEvent(source, 0, is_down)
        Quartz.CGEventKeyboardSetUnicodeString(e, 1, "z")
        Quartz.CGEventSetFlags(e, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)

def type_text(text: str):
    """Tape le texte via des événements unicode (champs sécurisés, terminaux).
    Fonctionne sans presse-papiers, quelle que soit la disposition clavier."""
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)
    for i in range(0, len(text), 16):
        chunk = text[i:i + 16]
        for is_down in (True, False):
            e = Quartz.CGEventCreateKeyboardEvent(source, 0, is_down)
            Quartz.CGEventKeyboardSetUnicodeString(e, len(chunk), chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
        time.sleep(0.004)

def paste_text(text: str, restore_clipboard: bool = True):
    """Colle `text` dans l'app active, puis restaure l'ancien presse-papiers
    (tous formats) si l'utilisateur n'a rien copié entre-temps."""
    previous = _snapshot_clipboard() if restore_clipboard else []
    _set_clipboard(text)
    pb = NSPasteboard.generalPasteboard()
    our_change = pb.changeCount()
    time.sleep(0.05)  # laisse le pasteboard se propager
    _press_cmd_v()

    if previous:

        def _restore():
            time.sleep(RESTORE_DELAY_S)
            if pb.changeCount() == our_change:  # l'utilisateur n'a pas copié autre chose
                try:
                    _restore_clipboard(previous)
                except Exception:
                    pass

        threading.Thread(target=_restore, daemon=True).start()

def copy_text(text: str):
    _set_clipboard(text)
