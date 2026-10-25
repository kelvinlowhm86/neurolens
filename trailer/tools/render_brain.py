"""Offline anatomical brain turntable, actual saved per-vertex predictions.
Binary parsing, parent interpolation, sulcal shading and heat stops follow static/brain_view.js.
No external renderer, service or display. All generated images stay in trailer/public/brain/.
"""
from pathlib import Path
import struct, os
os.environ['MPLCONFIGDIR']=str(Path(__file__).resolve().parents[1]/'.cache/matplotlib')
os.environ['XDG_CACHE_HOME']=str(Path(__file__).resolve().parents[1]/'.cache')
import numpy as np
from PIL import Image,ImageDraw,ImageFilter
from concurrent.futures import ProcessPoolExecutor
ROOT=Path(__file__).resolve().parents[1]
b=(ROOT/'public/fsaverage6_pial.bin').read_bytes(); V,F=struct.unpack_from('<II',b)
o=8; xyz=np.frombuffer(b,'<f4',V*3,o).reshape(-1,3).copy();o+=V*12
faces=np.frombuffer(b,'<u4',F*3,o).reshape(-1,3);o+=F*12
mask=np.frombuffer(b,'u1',V,o);o+=V
sulc=np.frombuffer(b,'<f4',V,o);o+=V*4
parents=np.frombuffer(b,'<u4',V*2,o).reshape(-1,2)
rows=np.fromfile(ROOT/'public/bbb_720p_55s/brain',dtype='<f2').reshape(-1,20484).astype(float)
xyz-=xyz.mean(axis=0)
# Fixed positive heat range, independent of frame; signed negative values remain neutral.
heat_range=float(np.quantile(rows,.99))
heat_x=np.array([0,.35,.65,.88,1]);heat_rgb=np.array([[.50,0,0],[.9,.1,.04],[1,.5,.04],[1,.85,.15],[1,1,.85]])
base=(.80+.17*(.5-.5*sulc))*np.where(mask,.82,1)
tri=xyz[faces];face_normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]);norm=np.zeros_like(xyz)
for k in range(3): np.add.at(norm,faces[:,k],face_normals)
norm/=np.maximum(np.linalg.norm(norm,axis=1)[:,None],1e-6)
OUT=ROOT/'public/brain';OUT.mkdir(exist_ok=True)
def frame(index):
 t=index/30; second=12+t
 # Slight orbit: a deliberate three-quarter lateral view.
 az=np.deg2rad(-51+10*(index/179));el=np.deg2rad(22)
 direction=np.array([np.cos(az)*np.cos(el),np.sin(az)*np.cos(el),np.sin(el)])
 right=np.cross([0,0,1],direction);right/=np.linalg.norm(right);up=np.cross(direction,right)
 projected=np.stack([xyz@right,xyz@up,xyz@direction],axis=1)
 pts=np.stack([600+projected[:,0]*5.4,510-projected[:,1]*5.4],axis=1)
 # Painter's algorithm, all triangles; rear triangles disappear behind front triangles.
 order=np.argsort(projected[faces,2].mean(axis=1))
 a=int(second);u=second-a;r=rows[a]*(1-u)+rows[min(a+1,len(rows)-1)]*u
 value=r[parents].mean(axis=1);shade=np.clip(value/heat_range,0,1);w=np.clip((shade-.04)/.12,0,1)*(1-mask)
 heat=np.stack([np.interp(shade,heat_x,heat_rgb[:,k]) for k in range(3)],axis=1)
 colour=np.repeat(base[:,None],3,axis=1);colour+=(heat-colour)*w[:,None]
 light=(direction*.68+up*.5+right*-.3);light/=np.linalg.norm(light)
 lighting=.58+.42*np.clip(norm@light,0,1)
 rgb=np.clip((colour*lighting[:,None])[faces].mean(axis=1)*255,0,255).astype('uint8')
 im=Image.new('RGBA',(1200,1020));draw=ImageDraw.Draw(im)
 for j in order:
  poly=pts[faces[j]].reshape(-1).tolist();draw.polygon(poly,fill=tuple(rgb[j])+(255,))
 # Antialias through downsampling; mild contour glow is only an optical treatment.
 im=im.resize((900,765),Image.Resampling.LANCZOS)
 im.save(OUT/f'{index:03d}.png')
 return index
if __name__=='__main__':
 import sys
 if '--test' in sys.argv:
  frame(90);sys.exit(0)
 print('Rendering',V,'vertices /',F,'triangles; heat range',heat_range,flush=True)
 with ProcessPoolExecutor(max_workers=3) as pool:
  for i in pool.map(frame,range(180),chunksize=3):
   if i%30==0: print('Brain frame',i,'/180',flush=True)
 (ROOT/'out/brain_provenance.json').write_text(__import__('json').dumps({'source':'data/output/bbb_720p_55s/brain','sourceSeconds':[12,18],'vertices':V,'triangles':F,'positiveHeatRange':heat_range,'colouring':'static/brain_view.js heat stops and parent interpolation'},indent=2))
