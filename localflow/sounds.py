"""Retours sonores de la dictée, préchargés en mémoire.

Avant, chaque son passait par `subprocess.Popen(["afplay", …])` : macOS devait
forker un processus, charger le binaire, ouvrir CoreAudio et décoder le fichier —
50 à 100 ms avant le premier échantillon. Sur un retour censé accompagner une
touche, ce décalage s'entend et casse la sensation de réactivité.

NSSound charge le fichier une fois au démarrage (`byReference:NO` = tout en RAM) ;
`play()` attaque alors immédiatement. On garde un petit pool par son : rejouer un
NSSound déjà en cours ne fait rien, il faut donc une instance libre sous la main
pour les appuis rapprochés.
"""

import os
import queue
import threading

from AppKit import NSSound

ASSETS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sounds")
START = "start"
STOP = "stop"
_POOL_SIZE = 3          # 3 suffit : au-delà, les sons se recouvrent en bouillie
VOLUME = 0.5


class _Pool:
    """Quelques copies d'un même son, jouées à tour de rôle."""

    def __init__(self, path):
        self.items = []
        for _ in range(_POOL_SIZE):
            s = NSSound.alloc().initWithContentsOfFile_byReference_(path, False)
            if s is None:
                break
            s.setVolume_(VOLUME)
            self.items.append(s)
        self.i = 0

    def play(self):
        if not self.items:
            return
        for _ in range(len(self.items)):        # d'abord une instance libre
            s = self.items[self.i]
            self.i = (self.i + 1) % len(self.items)
            if not s.isPlaying():
                s.play()
                return
        s = self.items[self.i]                  # toutes occupées : on relance la plus ancienne
        s.stop()
        s.play()


_pools = {}


def preload():
    """À appeler une fois au démarrage. Silencieux si un fichier manque."""
    for name in (START, STOP):
        path = os.path.join(ASSETS, name + ".wav")
        if name not in _pools and os.path.exists(path):
            try:
                _pools[name] = _Pool(path)
            except Exception:
                pass
    _warm()


def _warm():
    """Premier play() du processus : CoreAudio ouvre la sortie, ~90 ms. On paie ce
    prix ici, en muet, plutôt que sur le premier appui sur fn de l'utilisateur."""
    pool = _pools.get(START)
    if pool is None or not pool.items:
        return
    s = pool.items[0]
    try:
        s.setVolume_(0.0)
        s.play()
        s.stop()
    except Exception:
        pass
    finally:
        s.setVolume_(VOLUME)


_q = queue.Queue()
_thread = None


def _loop():
    while True:
        name = _q.get()
        pool = _pools.get(name)
        if pool is not None:
            try:
                pool.play()
            except Exception:
                pass


def play(name):
    """Retourne immédiatement. NSSound.play() coûte ~20 ms — plus d'une frame à
    60 fps — et `_play` est appelé depuis le thread principal, au moment exact où
    l'overlay démarre son animation. On le déporte pour ne pas hacher la frame."""
    global _thread
    if name not in _pools:
        preload()
    if _thread is None:
        _thread = threading.Thread(target=_loop, daemon=True)
        _thread.start()
    _q.put(name)
