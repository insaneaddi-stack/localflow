"""Écoute globale de la touche fn/globe via un CGEventTap.

Un event tap (plutôt que NSEvent monitors) permet d'AVALER fn+espace pour
qu'aucun espace ne soit inséré dans l'app active, et de détecter fn+autre
touche (fn+←, fn+⌫…) pour annuler un enregistrement déclenché par erreur.
Nécessite l'autorisation Accessibilité.

Le tap tourne sur SON PROPRE THREAD (run loop dédié) : même si le thread principal
de l'app se fige (modèle, UI…), macOS reçoit la réponse du tap immédiatement et
le clavier/trackpad ne sont jamais ralentis. Les callbacks sont donc appelés
depuis ce thread : l'app les renvoie sur le thread principal.
"""

import threading
import traceback

import Quartz

FN_KEYCODE = 63
SPACE_KEYCODE = 49
FLAG_FN = Quartz.kCGEventFlagMaskSecondaryFn

class FnListener:
    """Callbacks (appelés depuis le thread du tap — renvoyer sur le main thread) :

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
        self._loop = None
        self._thread = None

    def start(self):
        """Crée le tap sur un thread dédié ; lève si l'autorisation manque."""
        ready = threading.Event()
        self._error = None
        self._thread = threading.Thread(target=self._run, args=(ready,), daemon=True, name="fn-tap")
        self._thread.start()
        ready.wait(5.0)
        if self._error is not None:
            raise RuntimeError(self._error)
        if self._tap is None:
            raise RuntimeError("event tap : démarrage trop lent")

    def _run(self, ready):
        try:
            mask = (1 << Quartz.kCGEventKeyDown) | (1 << Quartz.kCGEventFlagsChanged)
            tap = Quartz.CGEventTapCreate(
                Quartz.kCGSessionEventTap,
                Quartz.kCGHeadInsertEventTap,
                Quartz.kCGEventTapOptionDefault,
                mask,
                self._cb,
                None,
            )
            if tap is None:
                self._error = ("Impossible de créer l'event tap : accorde l'autorisation "
                               "Accessibilité (Réglages Système → Confidentialité et sécurité).")
                ready.set()
                return
            source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
            loop = Quartz.CFRunLoopGetCurrent()
            Quartz.CFRunLoopAddSource(loop, source, Quartz.kCFRunLoopCommonModes)
            Quartz.CGEventTapEnable(tap, True)
            self._tap, self._source, self._loop = tap, source, loop
            ready.set()
            Quartz.CFRunLoopRun()   # jusqu'à stop()
        except Exception as exc:
            self._error = str(exc)
            ready.set()
        finally:
            self._tap = None
            self._source = None
            self._loop = None

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
            if self._tap is not None:
                Quartz.CGEventTapEnable(self._tap, False)
            if self._loop is not None:
                Quartz.CFRunLoopStop(self._loop)
        except Exception:
            pass
        t = self._thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=2.0)
        self._tap = None
        self._source = None
        self._loop = None
        self._thread = None
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
