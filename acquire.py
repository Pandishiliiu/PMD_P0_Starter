"""Automatically change iPad patterns and acquire 26 raw-resolution PNGs via Daheng gxipy.
Must run while serve_ipad.py is active and iPad browser is awake.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.request import urlopen
import numpy as np
import cv2
from common import PATTERNS, write_json, imwrite_unicode


def request(path):
    with urlopen('http://127.0.0.1:8000' + path, timeout=5) as res:
        return json.loads(res.read().decode())


def set_if_supported(cam, feature, value):
    try:
        ob=getattr(cam,feature)
        if ob.is_implemented() and ob.is_writable():
            ob.set(value)
            print(feature,'=',value)
            return True
    except Exception as e:
        print('Cannot set',feature,':',str(e)[:160])
    return False


def to_gray(raw):
    # Mono8/Mono12: raw ndarray; for Bayer/colour cameras try SDK conversion.
    arr=raw.get_numpy_array()
    if arr is None: raise RuntimeError('Empty camera frame')
    if arr.ndim == 2: return arr.copy()
    if arr.ndim == 3:
        if arr.shape[2] == 3: return cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        if arr.shape[2] == 4: return cv2.cvtColor(arr, cv2.COLOR_RGBA2GRAY)
    raise RuntimeError(f'Unsupported image shape {arr.shape}; set camera PixelFormat to Mono8 or provide conversion.')


def grab_gray(stream, timeout=2000):
    """Capture a standalone ndarray using the API supported by the installed Galaxy SDK.

    Current Galaxy DLLs can expose GXDQBufEx but not GXGetImageEx. The gxipy
    DataStream.get_image() method then raises NameError because gx_get_image was
    never defined. In that case use dq_buf/q_buf and COPY before re-queuing.
    """
    from gxipy import gxwrapper

    if callable(getattr(gxwrapper, 'gx_get_image', None)):
        raw = stream.get_image(timeout=timeout)
        return None if raw is None else to_gray(raw)

    if not (callable(getattr(gxwrapper, 'gx_dq_buf', None))
            and callable(getattr(gxwrapper, 'gx_q_buf', None))):
        raise RuntimeError(
            'Galaxy SDK does not expose GXGetImageEx or GXDQBufEx/GXQBufEx. '
            'Use the gxipy version bundled with the installed Galaxy SDK '
            'or repair the Galaxy SDK installation.'
        )

    raw = stream.dq_buf(timeout=timeout)
    if raw is None:
        return None
    try:
        # dq_buf owns SDK-managed memory, which becomes invalid after q_buf.
        return to_gray(raw).copy()
    finally:
        stream.q_buf(raw)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',default='measurements/run01')
    ap.add_argument('--exposure-us',type=float,default=10000)
    ap.add_argument('--settle-s',type=float,default=0.75)
    ap.add_argument('--timeout-s',type=float,default=25)
    a=ap.parse_args()
    try:
        import gxipy as gx
    except ImportError as e:
        raise SystemExit('Import gxipy failed. Read README_CN.md and install Daheng Galaxy SDK Python API.') from e
    num,info=gx.DeviceManager().update_device_list(1000)
    if not num: raise SystemExit('No camera. Close GalaxyView; check camera and SDK.')
    manager=gx.DeviceManager()
    num, info=manager.update_device_list(1000)
    cam=manager.open_device_by_index(1)
    out=Path(a.output);out.mkdir(parents=True,exist_ok=True)
    try:
        set_if_supported(cam, 'TriggerMode', gx.GxSwitchEntry.OFF)
        set_if_supported(cam, 'ExposureAuto', gx.GxAutoEntry.OFF)
        set_if_supported(cam, 'GainAuto', gx.GxAutoEntry.OFF)
        set_if_supported(cam, 'ExposureTime', a.exposure_us)
        set_if_supported(cam, 'Gain', 0.0)
        cam.stream_on()
        print('Camera streaming. Ensure stable focus, exposure, iPad brightness, and pressure.')
        for i,p in enumerate(PATTERNS):
            request(f'/api/set?index={i}')
            deadline=time.monotonic()+a.timeout_s
            while True:
                s=request('/api/status')
                if s['ack_index']==i: break
                if time.monotonic()>deadline:
                    raise RuntimeError('iPad did not acknowledge new pattern. Check browser, LAN, and screen wake state.')
                time.sleep(0.15)
            time.sleep(a.settle_s)
            # discard queued frames so the image matches the iPad's current pattern
            for _ in range(3):
                old=grab_gray(cam.data_stream[0],timeout=3000)
                if old is None: raise RuntimeError('Empty frame while flushing')
            arr=grab_gray(cam.data_stream[0],timeout=3000)
            if arr is None: raise RuntimeError('Empty camera frame')
            if arr.dtype not in (np.dtype('uint8'), np.dtype('uint16')):
                raise RuntimeError(f'Unsupported dtype {arr.dtype}')
            ok=imwrite_unicode(out/(p['name']+'.png'),arr)
            if not ok: raise RuntimeError('Cannot write image')
            maxv=np.iinfo(arr.dtype).max
            saturation=np.mean(arr>=.98*maxv)
            print(f'{i+1:02}/{len(PATTERNS)} {p["name"]:11} {arr.shape} {arr.dtype} mean={arr.mean():.1f} sat={saturation*100:.2f}%')
            if i==0 or i==len(PATTERNS)-1:
                write_json(out/'metadata.json', {'camera':info[0], 'exposure_us': a.exposure_us,
                  'screen':s['browser'], 'pattern': 'cosine_4step_cycles_1_4_16',
                  'pattern_names':[x['name'] for x in PATTERNS],
                  'warning':'Check that camera auto-exposure/gain is disabled and no image clipping.'})
        print('DONE. Run: python decode.py --input',out)
    finally:
        try:cam.stream_off()
        finally:cam.close_device()
        try:request('/api/set?index=-1')
        except Exception:pass

if __name__=='__main__':main()
