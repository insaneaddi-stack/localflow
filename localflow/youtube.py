"""Transcript d'une vidéo YouTube : l'onglet ouvert dans Chrome (ou un lien copié).

1. Sous-titres déjà publiés par YouTube (manuels, sinon automatiques) via yt-dlp :
   instantané, quelle que soit la durée.
2. Sinon, on télécharge seulement l'audio et Qwen3-ASR le transcrit en local, par
   segments coupés sur les pauses (même découpe que les longues dictées).

Le texte part dans le presse-papier et dans ~/Documents/LocalFlow Transcripts/<titre>.md.
"""
import glob
import html
import os
import re
import shutil
import subprocess
import tempfile

import numpy as np

from .predecode import SR, find_split

FOLDER = os.path.expanduser("~/Documents/LocalFlow Transcripts")
YT_RE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/)|youtu\.be/)[\w-]{6,}[^\s]*")


def _bin(name):
    """launchd n'a pas Homebrew dans son PATH."""
    return shutil.which(name) or next((p for p in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}")
                                       if os.path.exists(p)), None)


def find_url(clipboard=""):
    """L'onglet actif de Chrome s'il est sur YouTube, sinon un lien YouTube du presse-papier."""
    try:
        url = subprocess.run(["osascript", "-e",
                              'tell application "Google Chrome" to get URL of active tab of front window'],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if YT_RE.match(url):
            return url
    except Exception:
        pass
    m = YT_RE.search(clipboard or "")
    return m.group(0) if m else None


def vtt_to_text(vtt):
    """VTT YouTube → texte suivi. Les sous-titres automatiques répètent chaque ligne
    deux ou trois fois (affichage en défilement) : on ne garde que le nouveau."""
    lines, last = [], ""
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or "-->" in line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or line.isdigit():
            continue
        line = html.unescape(re.sub(r"<[^>]+>", "", line)).strip()
        if not line or line == last:
            continue
        if last and line.startswith(last):
            line = line[len(last):].strip()
            if not line:
                continue
        lines.append(line)
        last = line
    text = " ".join(lines)
    return re.sub(r"\s+", " ", text).strip()


def _title(ytdlp, url):
    r = subprocess.run([ytdlp, "--no-warnings", "--skip-download", "--print", "%(title)s", url],
                       capture_output=True, text=True, timeout=60)
    return (r.stdout.strip().splitlines() or ["Vidéo YouTube"])[0]


def _subtitles(ytdlp, url, tmp):
    for flag in ("--write-subs", "--write-auto-subs"):
        subprocess.run([ytdlp, "--no-warnings", "--skip-download", flag, "--sub-langs", "fr.*,fr,en.*,en",
                        "--sub-format", "vtt", "-o", os.path.join(tmp, "sub.%(ext)s"), url],
                       capture_output=True, text=True, timeout=120)
        files = sorted(glob.glob(os.path.join(tmp, "sub*.vtt")), key=lambda f: (".fr" not in f, f))
        if files:
            text = vtt_to_text(open(files[0], encoding="utf-8", errors="ignore").read())
            if len(text.split()) > 20:
                return text, ("sous-titres" if flag == "--write-subs" else "sous-titres auto")
    return None, None


def _audio(ytdlp, ffmpeg, url, tmp):
    subprocess.run([ytdlp, "--no-warnings", "-f", "bestaudio", "-o", os.path.join(tmp, "a.%(ext)s"), url],
                   capture_output=True, text=True, timeout=1800)
    src = next(iter(glob.glob(os.path.join(tmp, "a.*"))), None)
    if not src:
        return None
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", src, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, timeout=1800).stdout
    return np.frombuffer(raw, dtype=np.float32)


def transcribe_local(audio, transcribe, progress=None):
    """Découpe sur les pauses (≤ 45 s) et transcrit morceau par morceau."""
    texts, c = [], 0
    while c < len(audio):
        cut = find_split(audio, c, force=True) if len(audio) - c > 46 * SR else None
        end = cut or len(audio)
        t = transcribe(audio[c:end])
        if t:
            texts.append(t)
        c = end
        if progress:
            progress(c / len(audio))
    return " ".join(texts)


def fetch(url, transcribe=None, progress=None):
    """Renvoie (titre, texte, source). `transcribe(audio)` sert de repli sans sous-titres."""
    ytdlp, ffmpeg = _bin("yt-dlp"), _bin("ffmpeg")
    if not ytdlp:
        raise RuntimeError("yt-dlp introuvable : brew install yt-dlp")
    with tempfile.TemporaryDirectory() as tmp:
        title = _title(ytdlp, url)
        text, source = _subtitles(ytdlp, url, tmp)
        if text:
            return title, text, source
        if transcribe is None or not ffmpeg:
            raise RuntimeError("pas de sous-titres sur cette vidéo")
        audio = _audio(ytdlp, ffmpeg, url, tmp)
        if audio is None or not len(audio):
            raise RuntimeError("audio introuvable")
        return title, transcribe_local(audio, transcribe, progress), "transcrit en local"


def save(title, url, text, source):
    os.makedirs(FOLDER, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:90] or "Vidéo YouTube"
    path = os.path.join(FOLDER, f"{safe}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# {title}\n\n{url}  \n_{source} · {len(text.split())} mots_\n\n{text}\n")
    return path
