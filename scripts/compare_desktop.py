"""Offline, aligned comparison. No stretching or registration of individual blocks."""
from pathlib import Path
import argparse,json
from PIL import Image,ImageChops,ImageEnhance,ImageStat
root=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('actual');a=p.parse_args()
out=root/'data/desktop_visual_acceptance'
ref=Image.open(out/'reference-crop.png').convert('RGB');actual=Image.open(a.actual).convert('RGB')
assert actual.size==ref.size,(actual.size,ref.size)
actual.save(out/'after.png')
Image.blend(ref,actual,.5).save(out/'overlay-50.png')
diff=ImageChops.difference(ref,actual);diff.save(out/'diff.png')
ImageEnhance.Brightness(diff).enhance(3).save(out/'diff-3x.png')
side=Image.new('RGB',(ref.width*2,ref.height));side.paste(ref,(0,0));side.paste(actual,(ref.width,0));side.save(out/'side-by-side.png')
regions={'left':(17,66,260,944),'hero':(274,66,1152,546),'cards':(300,556,1126,691),'composer':(295,832,1131,934),'live':(1169,66,1506,944)}
metrics={k:{'mean_absolute_channel_difference':round(sum(ImageStat.Stat(diff.crop(box)).mean)/3,2)} for k,box in regions.items()}
(out/'comparison.json').write_text(json.dumps(metrics,indent=2))
print(json.dumps(metrics))
