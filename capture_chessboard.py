"""Preview and save direct chessboard images with Daheng camera. Space=save, Q=quit."""
import argparse
from pathlib import Path
import cv2
import numpy as np
from acquire import set_if_supported, grab_gray
from common import imwrite_unicode


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='calibration/camera_images')
    ap.add_argument('--exposure-us',type=float,default=10000)
    args=ap.parse_args()
    try:
        import gxipy as gx
    except ImportError as e:
        raise SystemExit('Cannot import gxipy. See README_CN.md') from e
    manager=gx.DeviceManager()
    n,info=manager.update_device_list(1000)
    if n<1:raise SystemExit('No camera detected; close GalaxyView first.')
    cam=manager.open_device_by_index(1)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    i=len(list(out.glob('board_*.png')))
    try:
        set_if_supported(cam,'TriggerMode',gx.GxSwitchEntry.OFF)
        set_if_supported(cam,'ExposureAuto',gx.GxAutoEntry.OFF)
        set_if_supported(cam,'GainAuto',gx.GxAutoEntry.OFF)
        set_if_supported(cam,'ExposureTime',args.exposure_us)
        cam.stream_on()
        print('SPACE to save original frame; q to quit. Vary chessboard tilt, position and distance.')
        while True:
            im=grab_gray(cam.data_stream[0],timeout=2000)
            if im is None:continue
            if im.dtype==np.uint16:
                disp=cv2.convertScaleAbs(im,alpha=255.0/max(float(np.percentile(im,99.9)),1))
            else: disp=im
            h,w=disp.shape[:2];disp=cv2.resize(disp,(max(1,int(w*min(1,960/w))),max(1,int(h*min(1,960/w)))))
            cv2.putText(disp,f'SPACE save board #{i+1} | q quit',(12,25),cv2.FONT_HERSHEY_SIMPLEX,0.6,255,2)
            cv2.imshow('Daheng chessboard calibration -- camera sees board DIRECTLY',disp)
            key=cv2.waitKey(1)&0xFF
            if key==ord('q'):break
            if key==ord(' '):
                i+=1;file=out/f'board_{i:02d}.png'
                imwrite_unicode(file,im)
                print('Saved',file)
    finally:
        try:cam.stream_off()
        finally:cam.close_device()
        cv2.destroyAllWindows()

if __name__=='__main__':main()
