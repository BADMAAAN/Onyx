"""Offline extraction of user-authorized, text-free decoration. Never run at startup."""
from pathlib import Path
import hashlib
import math
from PIL import Image, ImageDraw, ImageChops, ImageStat

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'design/reference/onyx-desktop-approved.png'
assert hashlib.sha256(source.read_bytes()).hexdigest() == '2a95ab5d834760d0b84bab4e3d68fba64283813890054e86d6cde3df0a522382'
im = Image.open(source).convert('RGB')
out = ROOT / 'onyx_desktop/assets'
report = ROOT / 'data/desktop_visual_acceptance'
report.mkdir(parents=True, exist_ok=True)
im.crop((8,11,1528,975)).save(report/'reference-crop.png')

def interpolate_box(image, box):
    # Explicitly bounded reconstruction, using only adjacent decorative pixels.
    x0,y0,x1,y1=box
    for x in range(x0,x1):
        top=image.getpixel((x,y0-1));bottom=image.getpixel((x,y1))
        for y in range(y0,y1):
            t=(y-y0+1)/(y1-y0+1)
            image.putpixel((x,y),tuple(round(a*(1-t)+b*t) for a,b in zip(top,bottom)))

import numpy as np
from scipy.sparse import lil_matrix
from scipy.sparse.linalg import spsolve

# Harmonic fill avoids column/row streaks from the hidden text layer. The
# planet edge and all pixels outside these explicit masks remain original.
pixels=np.array(im,dtype=float)
mask=np.zeros(pixels.shape[:2],dtype=bool)
for x0,y0,x1,y1 in [(383,260,824,437),(442,468,1004,505)]:mask[y0:y1,x0:x1]=True
ys,xs=np.where(mask);indices=np.full(mask.shape,-1);indices[ys,xs]=np.arange(len(ys))
matrix=lil_matrix((len(ys),len(ys)));rhs=np.zeros((len(ys),3))
for i,(y,x) in enumerate(zip(ys,xs)):
    matrix[i,i]=4
    for dy,dx in [(-1,0),(1,0),(0,-1),(0,1)]:
        j=indices[y+dy,x+dx]
        if j>=0:matrix[i,j]=-1
        else:rhs[i]+=pixels[y+dy,x+dx]
pixels[ys,xs]=spsolve(matrix.tocsr(),rhs)
planet=Image.fromarray(np.clip(pixels,0,255).astype('uint8'))
planet.crop((283,78,1159,560)).save(out/'planet.png')
im.crop((283,702,1159,842)).save(out/'center-lower.png')
def feather(image,amount):
    image=image.convert('RGBA')
    for y in range(image.height):
        for x in range(image.width):
            d=min(x,y,image.width-1-x,image.height-1-y)
            r,g,b,_=image.getpixel((x,y));image.putpixel((x,y),(r,g,b,round(255*min(1,d/amount))))
    return image
feather(im.crop((66,100,222,238)),18).save(out/'pearl.png')
im.crop((1178,562,1513,952)).save(out/'waves.png')
ring=im.crop((1189,196,1506,484))
# Remove the reference's microphone: only a decorative ring is retained.
# Reconstruct center using radial averages outside the glyph, no text/UI pixels.
cx,cy=158,157
for y in range(ring.height):
    for x in range(ring.width):
        r=math.hypot(x-cx,y-cy)
        if r<62:
            t=r/62
            ring.putpixel((x,y),tuple(round(a*(1-t)+b*t) for a,b in zip((12,20,40),(14,24,43))))
ring=feather(ring,24)
for y in range(ring.height):
    for x in range(ring.width):
        r=math.hypot(x-cx,y-cy)
        if r<75:
            v=ring.getpixel((x,y));ring.putpixel((x,y),(*v[:3],round(v[3]*max(0,(r-55)/20))))
ring.save(out/'live-ring.png')
small=feather(im.crop((1046,852,1132,938)),8)
for y in range(86):
    for x in range(86):
        r=math.hypot(x-42,y-42)
        if r<30:
            v=small.getpixel((x,y));small.putpixel((x,y),(*v[:3],round(v[3]*max(0,(r-24)/6))))
small.save(out/'composer-ring.png')
glow=Image.new('RGBA',(128,128))
for y in range(128):
    for x in range(128):
        a=math.exp(-((x-64)**2+(y-64)**2)/800)*65
        glow.putpixel((x,y),(48,108,246,round(a)))
glow.save(out/'mic-glow.png')
print('Canonical:',im.size,'crop:',(8,11,1528,975))
for name,box in {'window':(1000,22,1100,45),'left':(35,780,45,800),'center':(310,740,370,780),'live':(1200,800,1240,850),'card':(460,579,481,620),'input':(850,874,930,915)}.items():
    print(name,tuple(round(v) for v in ImageStat.Stat(im.crop(box)).median))

icons={
 'plus':'<path d="M12 3v18M3 12h18"/>',
 'minimize':'<path d="M3 12h18"/>',
 'maximize':'<rect x="4" y="4" width="16" height="16" rx="1"/>',
 'close':'<path d="m4 4 16 16M20 4 4 20"/>',
 'chat':'<path d="M5 3h14a3 3 0 0 1 3 3v10a3 3 0 0 1-3 3H9l-6 4v-5a3 3 0 0 1-1-2V6a3 3 0 0 1 3-3Z"/>',
 'folder':'<path d="M2 7V4a2 2 0 0 1 2-2h5l3 4h8a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7h7"/>',
 'mic':'<rect x="8.5" y="2" width="7" height="14" rx="3.5" fill="url(#light)" stroke="none"/><path d="M5 12v1a7 7 0 0 0 14 0v-1M12 20v3M8.5 23h7"/>',
 'mic-outline':'<rect x="8" y="1" width="8" height="16" rx="4"/><path d="M4 12v2a8 8 0 0 0 16 0v-2M12 22v3M8 25h8"/>',
 'settings':'<path d="m10 2-1 3-3-1-2 3 2 2-3 2v3l3 1-1 3 3 2 2-2 2 3h3l1-3 3 1 2-3-2-2 3-2v-3l-3-1 1-3-3-2-2 2-2-3Z"/><circle cx="12" cy="12" r="3.5"/>',
 'archive':'<path d="M5 6h14M9 3h6M7 6l1 16h8l1-16M10 9v10M14 9v10"/>',
 'idea':'<path d="M8 19h8M9 23h6M8 16c0-3-4-3-4-8a8 8 0 0 1 16 0c0 5-4 5-4 8v3H8Z"/>',
 'code':'<path d="m6 5-5 7 5 6M18 5l5 7-5 6M15 2l-6 21"/>',
 'document':'<path d="M14 1H5a2 2 0 0 0-2 2v19a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V8l-7-7v7h7M7 13h10M7 18h10"/>',
 'image':'<rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 18 5-5 4 3 4-5 6 6"/>',
 'wave':'<path d="M2 12h1M6 7v10M11 1v22M16 6v12M21 11h1"/>',
 'send':'<path d="m3 3 19 9-19 9 4-9-4-9ZM7 12h15"/>',
 'stop':'<rect x="5" y="5" width="14" height="14" rx="2"/>',
 'more':'<circle cx="4" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="20" cy="12" r="1"/>'
}
(out/'icons').mkdir(exist_ok=True)
for name,shape in icons.items():
    (out/'icons'/f'{name}.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 25"><defs><linearGradient id="light" x2="0" y2="1"><stop stop-color="#d8e6ff"/><stop offset="1" stop-color="#87d5ff"/></linearGradient></defs><g fill="none" stroke="'+('#a8daff' if name=='mic' else '#bbc8ff')+'" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">'+shape+'</g></svg>',encoding='utf-8')
