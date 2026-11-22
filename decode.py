"""Four-step phase -> hierarchical absolute screen coordinate; not metric height reconstruction."""
import argparse
import json
from pathlib import Path
import numpy as np
import cv2
from common import imread_unicode
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def phase4(im):
    # I(k) = A + B cos(theta + k*pi/2)
    i0,i1,i2,i3 = [im[k].astype(np.float32) for k in range(4)]
    ph=np.arctan2(i3-i1,i0-i2)
    amp=np.hypot(i3-i1,i0-i2)/2
    return np.mod(ph/(2*np.pi),1.), amp


def unwrap_temporal(fracs, freqs=(1,4,16)):
    u=fracs[0]
    for f,frac in zip(freqs[1:],fracs[1:]):
        k=np.rint(f*u-frac)
        u=(k+frac)/f
    return u


def decode_folder(directory, show=True):
    d=Path(directory)
    def read(name):
        img=imread_unicode(d/(name+'.png'),cv2.IMREAD_UNCHANGED)
        if img is None:raise FileNotFoundError(d/(name+'.png'))
        if img.ndim==3: img=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        return img
    black=read('black'); white=read('white')
    signal=white.astype(np.float32)-black.astype(np.float32)
    us=[]; amps=[]
    for ax in ('x','y'):
        fraclist=[]; alist=[]
        for f in (1,4,16):
            frames=[read(f'{ax}_f{f}_p{k}') for k in range(4)]
            frac,amp=phase4(frames)
            fraclist.append(frac); alist.append(amp)
        u=unwrap_temporal(fraclist)
        us.append(u); amps.append(np.min(np.stack(alist),axis=0))
    med=np.percentile(np.maximum(signal,0),95)
    min_mod=max(3.,0.03*med)
    mask=(signal>max(5.,0.08*med))&(amps[0]>min_mod)&(amps[1]>min_mod)
    mask &= (us[0]>=-0.01)&(us[0]<=1.01)&(us[1]>=-0.01)&(us[1]<=1.01)
    mask=cv2.morphologyEx(mask.astype(np.uint8),cv2.MORPH_OPEN,np.ones((3,3),np.uint8)).astype(bool)
    if mask.sum()<200:
        print('WARNING: very few valid pixels; inspect exposure, screen image, focus and mask threshold.')
    result=d/'decoded';result.mkdir(exist_ok=True)
    np.savez_compressed(result/'screen_uv.npz',u=us[0],v=us[1],mask=mask,mod_x=amps[0],mod_y=amps[1])
    for arr,name in [(us[0],'screen_u'),(us[1],'screen_v'),(np.minimum(amps[0],amps[1]),'modulation'),(mask,'mask')]:
        fig,ax=plt.subplots(figsize=(8,5)); im=ax.imshow(np.where(mask,arr,np.nan) if name!='mask' else arr,cmap='viridis');
        ax.set_title(name+' (P0: display coordinate, NOT measured height)');plt.colorbar(im,ax=ax)
        fig.savefig(result/(name+'.png'),dpi=140,bbox_inches='tight');plt.close(fig)
    diag={'input':str(d),'size':[int(signal.shape[1]),int(signal.shape[0])],
          'valid_fraction':round(float(mask.mean()),4), 'median_signal_on_valid':float(np.median(signal[mask])) if mask.any() else None,
          'range_u_valid':[float(us[0][mask].min()),float(us[0][mask].max())] if mask.any() else None,
          'range_v_valid':[float(us[1][mask].min()),float(us[1][mask].max())] if mask.any() else None}
    (result/'diagnostics.json').write_text(json.dumps(diag,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(diag,indent=2,ensure_ascii=False));print('Saved',result)
    return diag


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);a=ap.parse_args();decode_folder(a.input)
