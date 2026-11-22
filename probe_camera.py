"""Daheng gxipy probe. Close GalaxyView before this script."""
import sys
try:
    import gxipy as gx
except Exception as e:
    print('Cannot import gxipy:', e)
    print('Install Daheng GalaxySDK Python gxipy or, if needed, pip install iai-gxipy')
    sys.exit(2)
try:
    manager = gx.DeviceManager()
    num, infos = manager.update_device_list(1000)
    print('Daheng cameras found:', num)
    for i, info in enumerate(infos):
        print('camera', i, info)
    if not num:
        sys.exit('No camera detected. Check GalaxyView and driver, and close GalaxyView before retrying.')
    cam = manager.open_device_by_index(1)
    print('Opened camera #1')
    for feature in ('PixelFormat', 'ExposureTime', 'Gain', 'TriggerMode'):
        try:
            x = getattr(cam, feature)
            print(feature, x.get())
        except Exception as e:
            print(feature, 'unavailable:', str(e)[:200])
    cam.close_device()
except Exception as e:
    print('Camera/SDK error:', e)
    sys.exit(1)
