"""Local contact sheets for the final two-second audit frames."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
root=Path(__file__).resolve().parents[1]
frames=sorted((root/'out/frames').glob('frame_*.jpg'))
font=ImageFont.load_default(size=23)
for start in range(0,len(frames),8):
 batch=frames[start:start+8]
 sheet=Image.new('RGB',(1600,1928),'#17181B');draw=ImageDraw.Draw(sheet)
 for i,p in enumerate(batch):
  x=(i%2)*800;y=(i//2)*482
  draw.text((x+16,y+6),f'{(start+i)*2:02d}s',font=font,fill='#A0A6AF')
  with Image.open(p) as im: sheet.paste(im.resize((800,450),Image.Resampling.LANCZOS),(x,y+32))
 sheet.save(root/'out'/f'contact_{start//8+1}.jpg',quality=95)
print('Contact sheets:',len(frames),'frames, 0–42 seconds at two-second intervals.')
