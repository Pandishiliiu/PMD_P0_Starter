"""Diagnose PMD camera intrinsic calibration with constrained models.

Reads chessboard images, compares standard 5-distortion model with physically
plausible square-pixel models (no distortion / k1 / k1+k2). Does not overwrite
camera_intrinsics.npz. Run from PMD_P0_Starter.
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np


def read_gray_unicode(path):
    buf = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)


def rms_per_image(objpoints, imgpoints, K, D, rvecs, tvecs):
    vals = []
    for obj, corners, r, t in zip(objpoints, imgpoints, rvecs, tvecs):
        pred, _ = cv2.projectPoints(obj, r, t, K, D)
        v = pred.reshape(-1, 2) - corners.reshape(-1, 2)
        vals.append(float(np.sqrt(np.mean(np.sum(v*v, axis=1)))))
    return vals


def run():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--images',default='calibration/camera_images')
    p.add_argument('--square-mm',type=float,default=4.0)
    p.add_argument('--inner-cols',type=int,default=9)
    p.add_argument('--inner-rows',type=int,default=6)
    p.add_argument('--out-dir', default='calibration/diagnostic_models')
    a=p.parse_args()
    files=sorted(f for f in Path(a.images).iterdir() if f.suffix.lower() in {'.png','.jpg','.jpeg','.bmp','.tif','.tiff'})
    if not files:
        raise SystemExit(f'No photos in {a.images}')
    obj=np.zeros((a.inner_cols*a.inner_rows,3),np.float32)
    obj[:,:2]=np.mgrid[0:a.inner_cols,0:a.inner_rows].T.reshape(-1,2)*a.square_mm
    ops,ips,names=[],[],[]
    size=None
    for f in files:
        im=read_gray_unicode(f)
        if im is None:
            print('SKIP unreadable:',f.name)
            continue
        cur_size=(im.shape[1],im.shape[0])
        if size is None: size=cur_size
        if cur_size!=size:
            raise ValueError(f'Mixed image sizes: {f.name} {cur_size} vs {size}')
        if hasattr(cv2,'findChessboardCornersSB'):
            ok,corners=cv2.findChessboardCornersSB(im,(a.inner_cols,a.inner_rows))
        else:
            ok,corners=cv2.findChessboardCorners(im,(a.inner_cols,a.inner_rows))
            if ok:
                criteria=(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,40,0.001)
                corners=cv2.cornerSubPix(im,corners,(7,7),(-1,-1),criteria)
        if ok:
            ops.append(obj.copy())
            ips.append(corners.astype(np.float32))
            names.append(f.name)
        else:
            print('SKIP corners not found:',f.name)
    print(f'Photos: {len(files)}, usable: {len(names)}, image size: {size}')
    if len(names)<10:
        raise SystemExit('Need >=10 usable views')
    outdir=Path(a.out_dir)
    outdir.mkdir(parents=True,exist_ok=True)
    width,height=size
    # Nominal 50 mm lens on 1.85 um-pitch sensor. Initial guess only;
    # the solver estimates f, including effects of focus.
    f_guess=50.0/0.00185
    K_guess=np.array([[f_guess,0,(width-1)/2],
                      [0,f_guess,(height-1)/2],
                      [0,0,1.0]],dtype=np.float64)
    base=(cv2.CALIB_USE_INTRINSIC_GUESS | cv2.CALIB_FIX_ASPECT_RATIO | cv2.CALIB_ZERO_TANGENT_DIST)
    models=[
      ('free5',None,0),
      ('square_k1k2',K_guess,base | cv2.CALIB_FIX_K3),
      ('square_k1',K_guess,base | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3),
      ('square_zero',K_guess,base | cv2.CALIB_FIX_K1 | cv2.CALIB_FIX_K2 | cv2.CALIB_FIX_K3),
    ]
    print('\nThe lowest RMS is not automatically the best physical model!')
    for label,Kinit,flags in models:
        try:
            rms,K,D,rvecs,tvecs=cv2.calibrateCamera(ops,ips,size,
                            None if Kinit is None else Kinit.copy(), None,flags=flags)
        except cv2.error as e:
            print(label, 'OpenCV ERROR',str(e).splitlines()[0]); continue
        errors=rms_per_image(ops,ips,K,D,rvecs,tvecs)
        tilts=[]
        for rv in rvecs:
            R,_=cv2.Rodrigues(rv)
            z=np.clip(abs(R[2,2]),0,1)
            tilts.append(float(np.rad2deg(np.arccos(z))))
        print('\n==',label,'==')
        print(f'RMS {rms:.4f} px | fx {K[0,0]:.1f}, fy {K[1,1]:.1f}, fx/fy {K[0,0]/K[1,1]:.4f}, cx {K[0,2]:.1f}, cy {K[1,2]:.1f}')
        print('distortion',np.round(D.reshape(-1),6).tolist())
        print(f'Per-image RMS min/median/max: {np.min(errors):.3f} / {np.median(errors):.3f} / {np.max(errors):.3f} px')
        print(f'Board tilt median/max: {np.median(tilts):.1f} / {np.max(tilts):.1f} degrees')
        print('Worst views:',[(names[i],round(errors[i],3)) for i in np.argsort(errors)[-3:][::-1]])
        np.savez_compressed(outdir/(label+'.npz'),K=K,dist=D,image_size=np.asarray(size),square_mm=a.square_mm)
        record={'model':label,'images_used':names,'opencv_rms_px':float(rms),
            'K':K.tolist(),'dist':D.reshape(-1).tolist(),
            'image_reprojection_rms_px':dict(zip(names,errors)),
            'board_tilt_deg':dict(zip(names,tilts))}
        (outdir/(label+'.json')).write_text(json.dumps(record,indent=2,ensure_ascii=False),encoding='utf8')
    print('\nDiagnostic results saved in:',outdir)
    print('Do NOT replace calibration/camera_intrinsics.npz yet. Share this output for review.')

if __name__=='__main__':
    run()
