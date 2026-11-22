"""Make printable A4 9x6 inner-corner chessboard, physical square 8 mm at 300 dpi."""
from PIL import Image, ImageDraw
from pathlib import Path
DPI=300
MM2PX=DPI/25.4
W=int(round(210*MM2PX));H=int(round(297*MM2PX));s=round(8*MM2PX)
cols=10;rows=7
im=Image.new('L',(W,H),'white');dr=ImageDraw.Draw(im)
x0=(W-cols*s)//2;y0=(H-rows*s)//2
for y in range(rows):
 for x in range(cols):
  if (x+y)%2==0:dr.rectangle((x0+x*s,y0+y*s,x0+(x+1)*s-1,y0+(y+1)*s-1),fill=0)
path=Path(__file__).parent/'chessboard_A4_9x6_inner_8mm_300dpi.png';im.save(path,dpi=(DPI,DPI))
print('Saved',path,'(print at 100% / actual size, check square side with ruler = 8.00 mm)')
