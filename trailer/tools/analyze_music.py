"""Offline onset analysis. Input decoded by ffmpeg at 22050 Hz mono."""
from pathlib import Path
import json
import numpy as np
from scipy.signal import find_peaks
samples = np.fromfile('out/music.f32', dtype='<f4')
sr=22050; hop=220; win=1024
frames=np.lib.stride_tricks.sliding_window_view(samples,win)[::hop]
spec=np.abs(np.fft.rfft(frames*np.hanning(win),axis=1))
flux=np.maximum(0,np.diff(np.log1p(spec*5),axis=0)).mean(axis=1)
flux=np.r_[0,flux]; rms=np.sqrt((frames*frames).mean(axis=1))
peaks,_=find_peaks(flux,distance=int(.22*sr/hop),prominence=.012)
items=[{'time':round(float((p*hop+win/2)/sr),3),'strength':round(float(flux[p]),4),'rms':round(float(rms[p]),4)} for p in peaks]
Path('out/onsets.json').write_text(json.dumps(items,indent=2))
for s in range(0,80,4):
 ps=[x for x in items if s<=x['time']<s+4]
 print(s, ' '.join(f"{x['time']:.2f}({x['strength']:.2f})" for x in ps))
# Autocorrelation of onset envelope identifies the musical pulse.
f=flux-flux.mean(); ac=np.correlate(f[:7000],f[:7000],mode='full')[6999:]
lo,hi=int(.35*sr/hop),int(.8*sr/hop)
best=np.argsort(ac[lo:hi])[-5:]+lo
print('pulse candidates',[(round(x*hop/sr,4),round(60/(x*hop/sr),2)) for x in best[::-1]])
