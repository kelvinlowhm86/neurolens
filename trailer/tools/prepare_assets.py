"""Prepare local, auditable inputs: signed signals, font, exact music cut, real brain."""
from pathlib import Path
import json, numpy as np
root=Path(__file__).resolve().parents[1]
# Only fields used in the film enter the bundle; source JSON copies stay untouched.
for name,key in [('bbb_720p_55s','bunny'),('sintel_trailer_720p','sintel')]:
 d=json.loads((root/'public'/name/'result.json').read_text())
 (root/'src'/f'{key}.json').write_text(json.dumps({k:d[k] for k in ['duration_seconds','signals','muted']}))
onsets=json.loads((root/'out/onsets.json').read_text())
beats=[{'sourceSeconds':p['time'],'seconds':round(p['time']-34.5,3),'frame':round((p['time']-34.5)*30),'strength':p['strength']} for p in onsets if 34.5<=p['time']<77.5]
(root/'src/beats.ts').write_text('// Measured spectral-flux onsets. Source: public/music.mp3, 22050 Hz mono.\n'
 +'export const MUSIC_START = 34.5;\nexport const MUSIC_END = 77.5;\nexport const FPS = 30;\nexport const DURATION = 1290;\n'
 +'export const beats = '+json.dumps(beats,indent=2)+' as const;\n'
 +'// Phrase cuts within one video frame of measured downbeat peaks.\n'
 +'export const cuts = { open: 0, brain: 135, overall: 315, signals: 525, muted: 765, compare: 915, hero: 1095, credits: 1215, end: 1290 } as const;\n')
# Copy woff2 from the npm-installed font package. No Google Fonts HTTP request.
for weight in [400,500,600]:
 source=root/'node_modules/@fontsource/geist/files'/f'geist-latin-{weight}-normal.woff2'
 if source.exists():
  (root/'public'/f'geist-{weight}.woff2').write_bytes(source.read_bytes())
print('Prepared signed time series and',len(beats),'measured onsets.')
