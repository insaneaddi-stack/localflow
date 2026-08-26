#!/usr/bin/env python3
"""Génère les deux sons de LocalFlow dans assets/sounds/.

Les sons système de macOS (Tink, Pop) datent et sonnent « alerte ». On synthétise
à la place deux repères courts et accordés :

  start  ré6 + la6 (une quinte, brillante), attaque 3 ms, extinction 90 ms
  stop   la5 + ré6 (même intervalle, un ton plus bas), extinction 130 ms

Ce qui rend un son « premium » n'est pas sa richesse mais sa BRIÈVETÉ et la
netteté de son attaque : 3 ms suffisent à éviter le clic sans rien émousser.
La décroissance exponentielle (et non linéaire) est ce que fait un vrai corps
résonant — une rampe linéaire s'entend immédiatement comme synthétique.

    python3 helpers/make-sounds.py
"""

import math
import os
import struct
import wave

SR = 44100
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "sounds")

# (nom, fondamentale Hz, quinte Hz, durée s, tau d'extinction s, gain)
SOUNDS = [
    ("start", 1174.7, 1760.0, 0.16, 0.045, 0.55),   # ré6 + la6
    ("stop",   880.0, 1174.7, 0.20, 0.065, 0.42),   # la5 + ré6, plus bas et plus doux
]


def render(f0, f5, dur, tau, gain):
    n = int(SR * dur)
    attack = int(SR * 0.003)          # 3 ms : pas de clic, pas de mollesse
    out = []
    for i in range(n):
        t = i / SR
        env = math.exp(-t / tau)
        if i < attack:                 # rampe d'attaque en cosinus (sans discontinuité)
            env *= 0.5 - 0.5 * math.cos(math.pi * i / attack)
        s = (math.sin(2 * math.pi * f0 * t)
             + 0.35 * math.sin(2 * math.pi * f5 * t)
             + 0.08 * math.sin(2 * math.pi * 2 * f0 * t))   # harmonique : du corps, pas de la boue
        out.append(s * env * gain / 1.43)
    # fondu de sortie sur les 5 dernières ms : sinon le buffer se coupe net et claque
    fade = int(SR * 0.005)
    for i in range(fade):
        out[n - fade + i] *= 1.0 - i / fade
    return out


def write(path, samples):
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(
            struct.pack("<h", max(-32767, min(32767, int(s * 32767)))) for s in samples))


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, f0, f5, dur, tau, gain in SOUNDS:
        path = os.path.join(OUT, name + ".wav")
        write(path, render(f0, f5, dur, tau, gain))
        print(f"{path}  ({os.path.getsize(path)} octets, {dur * 1000:.0f} ms)")


if __name__ == "__main__":
    main()
