#!/bin/bash
# Auto-test LocalFlow — à passer AVANT toute relance et tout push. Ne touche ni au bundle ni à l'app qui tourne.
#   ./selftest.sh          tests rapides (~20 s)
#   ./selftest.sh --full   + transcription réelle, résumé Qwen, fenêtres hors écran (~2 min)
set -u
cd "$(dirname "$0")"
. ./env.sh
PY=.venv/bin/python
FULL=0; [ "${1:-}" = "--full" ] && FULL=1
fail=0
t() { if "$@" >/tmp/localflow-selftest.log 2>&1; then echo "  ✅ $NAME"; else echo "  ❌ $NAME"; tail -15 /tmp/localflow-selftest.log | sed 's/^/     /'; fail=1; fi; }

echo "LocalFlow — auto-test"
NAME="aucun crash macOS récent (.ips < 2 h)"; t sh -c '! find ~/Library/Logs/DiagnosticReports -maxdepth 1 -name "LocalFlow-*.ips" -mmin -120 2>/dev/null | grep . || { find ~/Library/Logs/DiagnosticReports -maxdepth 1 -name "LocalFlow-*.ips" -mmin -120 -exec basename {} \; ; false; }'
NAME="log : pas de traceback depuis le dernier démarrage"; t $PY -c "
import os,re; L=open(os.path.expanduser('~/.localflow.log')).read().splitlines(); i=max([k for k,l in enumerate(L) if 'démarrage:' in l] or [0]); bad=[l for l in L[i:] if 'Traceback' in l or 'CRASH' in l or 'erreur' in l.lower()]; print('\n'.join(bad[-5:])); assert not bad"
NAME="syntaxe (py_compile)";          t $PY -m py_compile localflow/*.py
NAME="imports de tous les modules";   t $PY -c "import localflow.app, localflow.meeting_window, localflow.meeting, localflow.summarize, localflow.sysaudio, localflow.meeting_detect, localflow.tutorial, localflow.history_window"
NAME="bash -n des scripts";           t bash -n install.sh setup.sh build-app.sh install-agent.sh run.sh update.sh uninstall.sh helpers/audiotap/build.sh
NAME="helper audiotap présent";       t test -x helpers/audiotap/audiotap
NAME="helper audiotap --probe";       t helpers/audiotap/audiotap --probe
NAME="Info.plist du bundle complet";  t sh -c 'plutil -p LocalFlow.app/Contents/Info.plist | grep -q NSMicrophoneUsageDescription && plutil -p LocalFlow.app/Contents/Info.plist | grep -q NSAudioCaptureUsageDescription && test -x LocalFlow.app/Contents/Helpers/audiotap'
NAME="signature du bundle";           t codesign --verify --deep --strict LocalFlow.app
NAME="config : défauts + sauvegarde"; t $PY -c "
from localflow.config import Config, DEFAULTS; c=Config(); [getattr(c,k) for k in ('cleanup_enabled','engine','meeting_summary_model','meeting_language','meeting_auto_detect')]"
NAME="micro : CoreAudio + ouverture + reset PortAudio"; t $PY -c "
import time
from localflow.audio import default_input_id, open_input_stream, close_input_stream, reset_portaudio, Recorder
assert default_input_id() > 0
r=Recorder(); r.open(); time.sleep(0.4); assert r.healthy(), 'flux mort'
reset_portaudio(); assert not r.healthy(), 'doit détecter la fermeture'
r.open(); time.sleep(0.4); assert r.healthy(); r.close()"
NAME="segmenteur réunion (VAD)";      t $PY -c "
import numpy as np
from localflow.meeting import _Segmenter, BLOCK, SAMPLE_RATE
s=_Segmenter('me'); out=[]
sig=np.concatenate([np.zeros(SAMPLE_RATE), 0.2*np.sin(np.arange(SAMPLE_RATE*2)*2*np.pi*440/SAMPLE_RATE), np.zeros(SAMPLE_RATE)]).astype(np.float32)
for i in range(0, len(sig)-BLOCK, BLOCK): out += s.feed(sig[i:i+BLOCK])
out += s.flush(); assert len(out)==1 and 1.5 < out[0][1]-out[0][0] < 3.6, out"
NAME="découpage du résumé (sections)"; t $PY -c "
from localflow.summarize import Summarizer
s=Summarizer.sections('## Résumé\nA\n## Décisions\n- b'); assert s['Résumé']=='A' and s['Décisions']=='- b'"
NAME="overlay : tous les états";      t $PY -c "
from AppKit import NSApplication; NSApplication.sharedApplication()
from localflow.overlay import Overlay
ov=Overlay(lambda:0.2, lambda:{'tiles':[{'title':'x','subtitle':'y','color':(1,0,0),'on':True,'action':'a'}]}, lambda a,p:None)
ov.meeting_info=lambda:{'clock':'01:23','sys_level':0.3,'offer':'Zoom'}
for st in ('meeting','meeting_offer','expanded','recording','processing','hover','idle'):
    ov._set_state(st); ov.content_alpha=1.0; ov.cur_w,ov.cur_h,_=ov._target_size(st); ov.view.display()"
NAME="fenêtres hors écran";           t $PY -c "
from AppKit import NSApplication; NSApplication.sharedApplication()
from localflow.meeting_window import LiveMeetingWindow, MeetingsWindow
from localflow.meeting import Meeting, MeetingIndex
from localflow.history_window import HistoryWindow
from localflow.config import Config
m=Meeting('t',title='T'); m.segments=[{'t0':1,'t1':2,'who':'me','text':'a'}]
lw=LiveMeetingWindow.alloc().initWithCallbacks_({'stop':lambda:None,'cancel':lambda:None,'notes':lambda t:None}); lw._build(); lw.meeting=m; lw.window.orderFront_(None); lw.refresh(); lw.close()
mw=MeetingsWindow.alloc().initWithIndex_callbacks_(MeetingIndex(),{'ask':lambda e,q:'','delete':lambda e:None,'notify':lambda a,b:None,'folder':lambda:'/tmp'}); mw._build(); mw.refresh()
hw=HistoryWindow.alloc().initWithConfig_notify_(Config(), lambda a,b:None); hw._build(); hw.refresh()"
if [ "$FULL" = 1 ]; then
NAME="Whisper : transcription réelle"; t env HF_HUB_OFFLINE=1 $PY -c "
import numpy as np, subprocess, wave, os
from localflow.transcribe import WhisperTranscriber
subprocess.run(['say','-v','Thomas','-o','/tmp/lf-say.wav','--data-format=LEI16@16000','Bonjour, ceci est un test de dictée locale.'],check=True)
w=wave.open('/tmp/lf-say.wav'); a=np.frombuffer(w.readframes(w.getnframes()),np.int16).astype(np.float32)/32768
t=WhisperTranscriber('whisper').transcribe(a, language='fr'); print(t); assert 'test' in t.lower() and 'dict' in t.lower()"
NAME="Qwen : résumé de réunion";      t env HF_HUB_OFFLINE=1 $PY -c "
from localflow.summarize import Summarizer
md=Summarizer('qwen-1.7b').summarize('Moi : on valide le budget de 30 000 euros pour septembre.\nEux : ok, César envoie la maquette vendredi.', notes='- budget')
print(md); s=Summarizer.sections(md); assert 'Résumé' in s and 'Actions' in s, md"
fi
if [ "$fail" = 0 ]; then echo "✅ Tout est vert."; else echo "❌ Corrige avant de relancer / pousser."; fi
exit $fail
