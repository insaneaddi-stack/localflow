"""Écoute globale de la touche fn/globe via un CGEventTap.

Un event tap (plutôt que NSEvent monitors) permet d'AVALER fn+espace pour
qu'aucun espace ne soit inséré dans l'app active, et de détecter fn+autre
touche (fn+←, fn+⌫…) pour annuler un enregistrement déclenché par erreur.
Nécessite l'autorisation Accessibilité. À démarrer depuis le thread principal.
"""

import traceback

import Quartz

FN_KEYCODE = 63
SPACE_KEYCODE = 49
FLAG_FN = Quartz.kCGEventFlagMaskSecondaryFn

class FnListener:
    """Callbacks (tous appelés sur le thread principal) :

    - on_down()      : fn vient d'être enfoncé
    - on_up()        : fn vient d'être relâché
    - on_fn_space()  : espace pressé pendant que fn est maintenu (avalé)
    - on_fn_other()  : une autre touche pressée pendant que fn est maintenu
    """

    def __init__(self, on_down, on_up, on_fn_space, on_fn_other, on_key=None):
        self.on_key = on_key      # on_key(keycode) -> True pour avaler la touche (panneau ouvert)
        self.on_down = on_down
        self.on_up = on_up
        self.on_fn_space = on_fn_space
        self.on_fn_other = on_fn_other
        self._pressed = False
        self._tap = None
        self._source = None
        self._cb = self._callback  # référence forte : PyObjC ne retient pas le callback

    def start(self):
        mask = (1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventFlagsChanged)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            self._cb,
            None,
        )
        if self._tap is None:
            raise RuntimeError(
                "Impossible de créer l'event tap : accorde l'autorisation "
                "Accessibilité (Réglages Système → Confidentialité et sécurité)."
            )
        self._source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes
        )
        Quartz.CGEventTapEnable(self._tap, True)

    def ensure_enabled(self):
        """À appeler périodiquement : macOS peut désactiver un tap (timeout,
        veille, changement de clavier). Le réactive ou le recrée."""
        try:
            if self._tap is None:
                self.start()
                return "recréé"
            if not Quartz.CGEventTapIsEnabled(self._tap):
                Quartz.CGEventTapEnable(self._tap, True)
                if not Quartz.CGEventTapIsEnabled(self._tap):
                    self.stop()
                    self.start()
                    return "recréé"
                return "réactivé"
        except Exception:
            traceback.print_exc()
            try:
                self.stop()
                self.start()
                return "recréé"
            except Exception:
                return "échec"
        return None

    def stop(self):
        try:
            if self._source is not None:
                Quartz.CFRunLoopRemoveSource(
                    Quartz.CFRunLoopGetMain(), self._source, Quartz.kCFRunLoopCommonModes
                )
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)
        except Exception:
            pass
        self._tap = None
        self._source = None
        self._pressed = False

    def release(self):
        """Oublie un fn « coincé » (ex. fn relâché pendant la veille)."""
        self._pressed = False

    def _callback(self, proxy, etype, event, refcon):
        try:
            if etype in (
                Quartz.kCGEventTapDisabledByTimeout,
                Quartz.kCGEventTapDisabledByUserInput,
            ):
                Quartz.CGEventTapEnable(self._tap, True)
                return event

            keycode = Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode
            )

            if etype == Quartz.kCGEventFlagsChanged:
                if keycode == FN_KEYCODE:
                    down = bool(Quartz.CGEventGetFlags(event) & FLAG_FN)
                    if down and not self._pressed:
                        self._pressed = True
                        self.on_down()
                    elif not down and self._pressed:
                        self._pressed = False
                        self.on_up()
                return event

            if etype == Quartz.kCGEventKeyDown and not self._pressed and self.on_key is not None:
                if self.on_key(keycode):
                    return None
            if etype == Quartz.kCGEventKeyDown and self._pressed:
                autorepeat = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventAutorepeat
                )
                if keycode == SPACE_KEYCODE:
                    if not autorepeat:
                        self.on_fn_space()
                    return None  # avalé : pas d'espace inséré
                if not autorepeat:
                    self.on_fn_other()
                return event
        except Exception:
            print("[hotkey] erreur dans le handler fn :", flush=True)
            traceback.print_exc()
        return event
