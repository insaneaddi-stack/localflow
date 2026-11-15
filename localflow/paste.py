"""Collage du texte dans l'app active : presse-papiers + Cmd+V synthétique."""

import threading
import time

import Quartz
from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

V_KEYCODE = 9  # touche 'v' (même position physique en AZERTY)
# Le presse-papiers d'origine est remis dès que le texte est vu dans le champ (relu via
# Accessibilité), au plus tard après RESTORE_MAX_S. 0,6 s fixe était trop court pour une
# app lente (Mac qui swappe) : elle lisait le presse-papiers APRÈS sa restauration et
# collait l'ancien contenu au lieu de la dictée.
RESTORE_MAX_S = 1.5
RESTORE_POLL_S = 0.1

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

def paste_text(text: str, restore_clipboard: bool = True, on_miss=None):
    """Colle `text` dans l'app active, puis restaure l'ancien presse-papiers
    (tous formats) si l'utilisateur n'a rien copié entre-temps.
    Collage raté (champ lisible mais sans le texte) : la dictée RESTE dans le
    presse-papiers et on_miss() prévient l'utilisateur."""
    from .learning import read_focused_text
    before = read_focused_text()
    previous = _snapshot_clipboard() if restore_clipboard else []
    _set_clipboard(text)
    pb = NSPasteboard.generalPasteboard()
    our_change = pb.changeCount()
    time.sleep(0.05)  # laisse le pasteboard se propager
    _press_cmd_v()

    def _restore():
        ok = wait_until_pasted(text, read=read_focused_text)
        now = read_focused_text()
        if not ok and before is not None and now == before:
            # Champ lisible et INCHANGÉ : le ⌘V n'a pas pris (vu dans Gemini). On retape
            # le texte directement ; inchangé = aucun risque de doublon.
            type_text(text)
            ok = wait_until_pasted(text, max_s=1.0, read=read_focused_text)
        if not ok:
            ok = not now                   # champ illisible ou modifié (envoyé…) : on fait confiance
        if not ok:
            if on_miss:
                on_miss()
            return                         # on garde la dictée dans le presse-papiers
        if previous and pb.changeCount() == our_change:  # l'utilisateur n'a pas copié autre chose
            try:
                _restore_clipboard(previous)
            except Exception:
                pass

    threading.Thread(target=_restore, daemon=True).start()

def pasted_in(text: str, field) -> bool:
    """Vrai si le champ relu contient la fin du texte collé (blancs ignorés : le champ
    rend le texte tel qu'affiché, retours à la ligne compris)."""
    if not field:
        return False
    tail = "".join(text.split())[-40:]
    return bool(tail) and tail in "".join(field.split())

def wait_until_pasted(text: str, max_s: float = RESTORE_MAX_S, read=None):
    """Attend que le collage soit visible dans le champ, au plus max_s. Rend True si vu.
    Champ illisible (VS Code, terminaux…) : on attend simplement max_s."""
    if read is None:
        from .learning import read_focused_text as read
    end = time.time() + max_s
    while time.time() < end:
        time.sleep(RESTORE_POLL_S)
        if pasted_in(text, read()):
            time.sleep(RESTORE_POLL_S)   # l'app finit de traiter Cmd+V
            return True
    return False

def copy_text(text: str):
    _set_clipboard(text)
