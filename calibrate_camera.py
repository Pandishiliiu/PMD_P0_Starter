"""OpenCV Zhang camera intrinsic calibration from direct (NOT reflected) chessboard photos."""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from common import imread_unicode, imwrite_unicode


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--images',required=True,help='folder with 15-25 chessboard views (*.png/jpg/tif)')
    ap.add_argument('--square-mm',type=float,default=8.0)
    ap.add_argument('--inner-cols',type=int,default=9)
    ap.add_argument('--inner-rows',type=int,default=6)
    ap.add_argument('--output',default='calibration/camera_intrinsics.npz')
    a=ap.parse_args()
    p=Path(a.images);files=sorted(q for q in p.iterdir() if q.suffix.lower() in ('.png','.jpg','.jpeg','.bmp','.tif','.tiff'))
    if not files:raise SystemExit('No calibration images in '+str(p))
    obj=np.zeros((a.inner_cols*a.inner_rows,3),np.float32)
    obj[:,:2]=np.mgrid[0:a.inner_cols,0:a.inner_rows].T.reshape(-1,2)*a.square_mm
    objects=[];image_pts=[];used=[];size=None
    debug=Path(a.output).parent/'board_detection';debug.mkdir(parents=True,exist_ok=True)
    for file in files:
        im=imread_unicode(file,cv2.IMREAD_GRAYSCALE)
        if im is None:print('skip unreadable',file);continue
        if size is None:size=(im.shape[1],im.shape[0])
        if size!=(im.shape[1],im.shape[0]):raise RuntimeError('Mixed image sizes; calibration cannot proceed.')
        if hasattr(cv2,'findChessboardCornersSB'):
            ok,corners=cv2.findChessboardCornersSB(im,(a.inner_cols,a.inner_rows))
        else:
            ok,corners=cv2.findChessboardCorners(im,(a.inner_cols,a.inner_rows),flags=cv2.CALIB_CB_ADAPTIVE_THRESH+cv2.CALIB_CB_NORMALIZE_IMAGE)
            if ok:corners=cv2.cornerSubPix(im,corners,(11,11),(-1,-1),(cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,30,.001))
        print(file.name,'corners' if ok else 'NOT FOUND')
        if ok:
            objects.append(obj.copy());image_pts.append(corners);used.append(file.name)
            vis=cv2.cvtColor(im,cv2.COLOR_GRAY2BGR);cv2.drawChessboardCorners(vis,(a.inner_cols,a.inner_rows),corners,ok)
            imwrite_unicode(debug/file.name,vis)
    if len(used)<10:raise SystemExit(f'Only {len(used)} usable images; need >=10 diverse poses, ideally 15-25.')
    rms,K,dist,rvecs,tvecs=cv2.calibrateCamera(objects,image_pts,size,None,None)
    errs=[]
    for o,impts,r,t in zip(objects,image_pts,rvecs,tvecs):
        calc,_=cv2.projectPoints(o,r,t,K,dist)
        errs.append(float(np.sqrt(np.mean(np.sum((calc.reshape(-1,2)-impts.reshape(-1,2))**2,axis=1)))))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(out,K=K,dist=dist,image_size=np.array(size),square_mm=a.square_mm)
    rep={'images_used':used,'image_size':size,'square_mm':a.square_mm,'opencv_rms_px':float(rms),
         'image_reprojection_rms_px':dict(zip(used,errs)),'K':K.tolist(),'dist':dist.reshape(-1).tolist()}
    out.with_suffix('.json').write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print('RMS (pixels)=',rms,'\nK=\n',K,'\nDist=',dist,'\nSaved:',out)
    if rms>0.5:print('WARNING: high residual; check square size, photos, focus, stable lens and board flatness.')

if __name__=='__main__':main()
