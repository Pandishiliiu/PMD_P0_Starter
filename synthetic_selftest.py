"""Checks 4-step multi-frequency decoder on synthetic, distortion-free phase images."""
import numpy as np
from decode import phase4, unwrap_temporal
H,W=71,113
U,V=np.meshgrid((np.arange(W)+0.3)/W,(np.arange(H)+0.2)/H)
for label,q in [('x',U),('y',V)]:
 fracs=[]
 for f in (1,4,16):
  imgs=[np.float32(120+70*np.cos(2*np.pi*(f*q+k/4))) for k in range(4)]
  frac,amp=phase4(imgs)
  fracs.append(frac)
 rec=unwrap_temporal(fracs)
 err=np.max(np.abs(rec-q))
 print(label,'max absolute error:',err)
 assert err<2e-6,(label,err)
print('PASS: PMD four-step multifrequency phase decoder.')
