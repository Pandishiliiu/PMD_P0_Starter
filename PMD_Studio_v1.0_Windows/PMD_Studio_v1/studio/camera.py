"""Single owning process for Daheng camera: preview and software-trigger capture."""
from __future__ import annotations
import os
import sys
import threading
import time
from pathlib import Path
import cv2
import numpy as np

# gxipy is supplied by Daheng Galaxy SDK, not by the ordinary web dependencies.
# PMD Studio may be extracted with an extra directory level, so looking only
# in parents[2] is insufficient. Discover a *bounded* set of local SDK paths.
import importlib


def _gxipy_roots():
    places = []
    override = os.environ.get('PMD_GXIPY_DIR', '').strip().strip('"')
    if override:
        places.append(Path(override))
    places.extend(Path(__file__).resolve().parents)
    places.extend(Path(sys.executable).resolve().parents)
    places.extend(Path.cwd().resolve().parents)
    places.append(Path.cwd().resolve())
    seen = set()
    for place in places:
        # The specified path may itself be the gxipy directory.
        choices = [place, place / 'Python', place / 'python',
                   place / 'sdk', place / 'SDK', place / 'lib',
                   place / 'GalaxySDK', place / 'vendor']
        for candidate in choices:
            try:
                candidate = candidate.resolve()
            except OSError:
                continue
            if candidate.name.lower() == 'gxipy':
                candidate = candidate.parent
            key = str(candidate).lower()
            if key not in seen:
                seen.add(key)
                yield candidate


def _load_gxipy():
    try:
        return importlib.import_module('gxipy')
    except ModuleNotFoundError as exc:
        if exc.name != 'gxipy':
            raise
    for folder in _gxipy_roots():
        if ((folder / 'gxipy' / '__init__.py').is_file() or
                (folder / 'gxipy.py').is_file()):
            if str(folder) not in sys.path:
                sys.path.insert(0, str(folder))
            try:
                return importlib.import_module('gxipy')
            except Exception as exc:
                raise RuntimeError(
                    '找到 gxipy，但导入失败：%s\nSDK目录：%s' % (exc, folder)
                ) from exc
    raise ModuleNotFoundError(
        '找不到大恒相机 Python SDK（gxipy）。请确认 gxipy 文件夹在旧 '
        'PMD_P0_Starter 根目录，或设置 PMD_GXIPY_DIR 为 gxipy 的上一级目录。'
        '请运行附带的 check_camera_sdk.bat 检查。'
    )

class Camera:
    def __init__(self):
        self.lock = threading.RLock()
        self.cam = None
        self.demo = os.environ.get('PMD_DEMO', '0') == '1'
        self.exposure = 10000.0
        self.previewing = False
        self.preview_thread = None
        self.frame_jpeg = None
        self.frame_time = 0
        self.frame_seq = 0
        self.preview_fps = 0.0
        # Separate condition for frame consumers: never block the camera SDK lock.
        self.frame_cond = threading.Condition()
        self.preview_max_fps = min(30., max(1., float(os.environ.get('PMD_PREVIEW_FPS', '24'))))
        self.preview_width = max(320, min(1440, int(os.environ.get('PMD_PREVIEW_WIDTH', '960'))))
        self.last_error = ''
        self.acquiring = False
        self.gx = None
        self.manager = None
        self.count = 0
        self.demo_mode = 'sample'

    def status(self):
        return {'connected': self.cam is not None or self.demo and self.count > 0,
                'demo': self.demo, 'exposure_us': self.exposure,
                'preview': self.previewing, 'acquiring': self.acquiring,
                'last_error': self.last_error, 'frame_ready': self.frame_jpeg is not None,
                'preview_fps': round(self.preview_fps, 1),
                'preview_width': self.preview_width}

    def connect(self):
        with self.lock:
            if self.cam is not None or self.demo and self.count:
                return self.status()
            if self.demo:
                self.count = 1
                return self.status()
            try:
                gx = _load_gxipy()
                self.gx = gx
                self.manager = gx.DeviceManager()
                count, _ = self.manager.update_device_list()
                if count < 1:
                    raise RuntimeError('未检测到大恒相机。请检查USB/驱动。')
                cam = self.manager.open_device_by_index(1)
                try:
                    cam.TriggerMode.set(gx.GxSwitchEntry.ON)
                    cam.TriggerSource.set(gx.GxTriggerSourceEntry.SOFTWARE)
                    try:
                        if cam.ExposureAuto.is_implemented():
                            cam.ExposureAuto.set(gx.GxAutoEntry.OFF)
                    except Exception:
                        pass
                    cam.ExposureTime.set(self.exposure)
                    cam.stream_on()
                except Exception:
                    try: cam.close_device()
                    finally: raise
                self.cam = cam
                self.count = count
                self.last_error = ''
                return self.status()
            except Exception as exc:
                self.last_error = str(exc)
                raise RuntimeError('相机连接失败：' + str(exc)) from exc

    def disconnect(self):
        self.stop_preview()
        with self.lock:
            if self.cam:
                try: self.cam.stream_off()
                finally: self.cam.close_device()
            self.cam = None
            self.count = 0
            with self.frame_cond:
                self.frame_jpeg = None
                self.frame_cond.notify_all()
            return self.status()

    def set_exposure(self, us):
        us = float(us)
        if not 20 <= us <= 1000000:
            raise ValueError('曝光时间必须在20—1000000 µs之间')
        with self.lock:
            self.exposure = us
            if self.cam:
                self.cam.ExposureTime.set(us)
        return self.status()

    def _demo_image(self, pattern=None, config=None):
        h,w = 720, 960
        y,x = np.mgrid[:h, :w].astype(np.float32)
        u = .13 + .74*x/w + .055*np.sin(2*np.pi*y/h)
        v = .17 + .68*y/h
        if self.demo_mode == 'sample':
            u += .025*((x-w/2)/(w/2))**2
            v += .035*((y-h/2)/(h/2))**2
        inside = (((x-w/2)/(w*.36))**2 + ((y-h/2)/(h*.37))**2) < 1
        illum = np.full((h,w), 22., dtype=np.float32)
        illum[inside] = 128
        if pattern is not None and config is not None:
            if pattern['type'] == 'black': illum[inside] = 0
            elif pattern['type'] == 'white': illum[inside] = 255
            else:
                coord = u if pattern['axis'] == 'x' else v
                illum[inside] = config['brightness'] + config['contrast'] * np.cos(
                    2*np.pi*(pattern['freq']*coord+pattern['phase']/4.0))[inside]
        noise = np.random.default_rng().normal(0,1.7,(h,w))
        return np.clip(illum+noise,0,255).astype(np.uint8)

    def _shoot_unlocked(self, pattern=None, config=None):
        if self.demo:
            return self._demo_image(pattern, config)
        if self.cam is None:
            raise RuntimeError('请先连接相机')
        # Drop any cached frames from earlier previews before new software trigger.
        try:
            stream = self.cam.data_stream[0]
            if hasattr(stream, 'flush_queue'):
                stream.flush_queue()
        except Exception:
            pass
        self.cam.TriggerSoftware.send_command()
        raw = self.cam.data_stream[0].get_image(timeout=5000)
        if raw is None:
            raise RuntimeError('相机触发后无图像；检查SDK及相机状态')
        arr = raw.get_numpy_array()
        if arr is None:
            raise RuntimeError('相机返回空图像')
        arr = arr.copy()
        if arr.ndim == 3:
            arr = cv2.cvtColor(arr,cv2.COLOR_BGR2GRAY)
        if arr.dtype != np.uint8:
            if arr.dtype == np.uint16:
                arr = np.clip(arr / 256, 0, 255).astype(np.uint8)
            else:
                arr = np.clip(arr,0,255).astype(np.uint8)
        return arr

    def shoot(self, pattern=None, config=None):
        with self.lock:
            return self._shoot_unlocked(pattern, config)

    def start_preview(self):
        if not self.status()['connected']:
            raise RuntimeError('请先连接相机')
        if self.acquiring:
            raise RuntimeError('自动采集时不能开启预览')
        if self.previewing:
            return self.status()
        self.preview_fps = 0.0
        with self.frame_cond:
            self.frame_jpeg = None
            self.frame_cond.notify_all()
        self.previewing = True
        self.preview_thread = threading.Thread(target=self._preview_worker, daemon=True)
        self.preview_thread.start()
        return self.status()

    def _preview_worker(self):
        """Soft-trigger live preview with a bounded FPS and latest-frame buffer.

        Only this worker touches preview frames. Acquisition still uses the full
        native sensor resolution (no preview downsampling in _shoot_unlocked).
        """
        last_frame_time = None
        interval = 1.0 / self.preview_max_fps
        try:
            while self.previewing:
                if self.acquiring:
                    time.sleep(.04)
                    continue
                frame_start = time.monotonic()
                with self.lock:
                    frame = self._shoot_unlocked()

                # Fast decimation BEFORE resize. On the 4024 x 3036 camera,
                # INTER_AREA from 12 MP per frame is unnecessarily expensive.
                height, width = frame.shape[:2]
                stride = max(1, int(width // self.preview_width))
                if stride > 1:
                    frame = frame[::stride, ::stride]
                if frame.shape[1] > self.preview_width:
                    preview_h = max(1, round(frame.shape[0] * self.preview_width / frame.shape[1]))
                    frame = cv2.resize(frame, (self.preview_width, preview_h),
                                       interpolation=cv2.INTER_LINEAR)
                ok, jpeg = cv2.imencode('.jpg', frame,
                                        [cv2.IMWRITE_JPEG_QUALITY, 78])
                if not ok:
                    raise RuntimeError('Preview JPEG encoding failed')

                now = time.monotonic()
                if last_frame_time is not None and now > last_frame_time:
                    fps = 1.0 / (now - last_frame_time)
                    self.preview_fps = (0.75*self.preview_fps + 0.25*fps
                                        if self.preview_fps else fps)
                last_frame_time = now
                with self.frame_cond:
                    self.frame_jpeg = jpeg.tobytes()
                    self.frame_time = now
                    self.frame_seq += 1
                    self.frame_cond.notify_all()

                # Cap load rather than sending redundant images at max speed.
                remaining = interval - (time.monotonic() - frame_start)
                if remaining > 0:
                    time.sleep(remaining)
        except Exception as exc:
            self.last_error = str(exc)
        finally:
            self.previewing = False
            self.preview_fps = 0.0
            with self.frame_cond:
                self.frame_cond.notify_all()

    def wait_preview_frame(self, after_seq: int, timeout: float = 1.0):
        """Return (seq, JPEG) for latest frame, skipping intermediate frames.

        A slow browser never queues stale frames, and does not hold self.lock.
        """
        with self.frame_cond:
            self.frame_cond.wait_for(
                lambda: ((self.frame_seq > after_seq and self.frame_jpeg is not None)
                         or not self.previewing),
                timeout=timeout,
            )
            if not self.previewing or self.frame_jpeg is None:
                return None
            if self.frame_seq <= after_seq:
                return None
            return self.frame_seq, self.frame_jpeg

    def stop_preview(self):
        self.previewing = False
        with self.frame_cond:
            self.frame_cond.notify_all()
        th=self.preview_thread
        if th is not None and th.is_alive() and th is not threading.current_thread():
            th.join(timeout=2)
        self.preview_thread=None

    def write_png(self, frame, dest):
        dest=Path(dest)
        dest.parent.mkdir(parents=True,exist_ok=True)
        ok,encoded=cv2.imencode('.png',frame)
        if not ok: raise RuntimeError('PNG编码失败')
        encoded.tofile(str(dest))
        return {'filename':dest.name, 'size':list(frame.shape[::-1]),
                'p99':float(np.percentile(frame,99)),
                'saturated_fraction':float(np.mean(frame>=254))}

camera=Camera()
