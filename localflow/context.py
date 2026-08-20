"""Contexte de l'app active : ton à adopter et stratégie de collage."""

import ctypes
import ctypes.util

from AppKit import NSWorkspace

CASUAL_APPS = {
    "com.tinyspeck.slackmacgap", "net.whatsapp.WhatsApp", "com.apple.MobileSMS",
    "ru.keepcoder.Telegram", "com.tdesktop.Telegram", "com.hnc.Discord",
    "com.apple.iChat", "com.facebook.archon", "com.apple.Messages",
}
FORMAL_APPS = {
    "com.apple.mail", "com.microsoft.Outlook", "com.readdle.smartemail-Mac",
    "com.apple.iWork.Pages", "com.microsoft.Word", "com.apple.Notes",
    "notion.id", "com.notion.Notion", "md.obsidian", "com.linear", "app.superhuman",
    "com.asana.app", "com.apple.TextEdit", "com.google.Chrome.app.gmail",
}
# Apps où Cmd+V est peu fiable : on tape le texte caractère par caractère
TYPE_APPS = {
    "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable",
    "com.mitchellh.ghostty", "io.alacritty", "net.kovidgoyal.kitty",
    "com.parallels.desktop.console", "com.vmware.fusion", "com.microsoft.rdc.macos",
}

_carbon = None

def secure_input_enabled() -> bool:
    """Vrai si un champ mot de passe (saisie sécurisée) a le focus."""
    global _carbon
    try:
        if _carbon is None:
            _carbon = ctypes.cdll.LoadLibrary(ctypes.util.find_library("Carbon"))
            _carbon.IsSecureEventInputEnabled.restype = ctypes.c_bool
        return bool(_carbon.IsSecureEventInputEnabled())
    except Exception:
        return False

def frontmost_app():
    """(bundle_id, nom) de l'app au premier plan, ou ('', '')."""
    try:
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return (app.bundleIdentifier() or "", app.localizedName() or "")
    except Exception:
        return ("", "")

def tone_for(bundle_id: str) -> str:
    if bundle_id in CASUAL_APPS:
        return "casual"
    if bundle_id in FORMAL_APPS:
        return "formal"
    return "neutral"

def should_type(bundle_id: str) -> bool:
    return bundle_id in TYPE_APPS or secure_input_enabled()
