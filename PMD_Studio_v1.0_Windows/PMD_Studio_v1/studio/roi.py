"""Per-measurement manually drawn image-space masks.

Coordinates in the metadata are normalized (0..1) relative to the *actual*
full-resolution white frame. A PNG with exactly that native resolution is
written to the measurement's own data unit; the decoder intersects it with
its quality mask. The calibration data are not changed.
"""
from __future__ import annotations
import math
from pathlib import Path
import cv2
import numpy as np
from .processing import img_read, img_save


def raw_image_shape(raw: Path) -> tuple[int, int]:
    image = img_read(Path(raw) / 'white.png')
    return int(image.shape[1]), int(image.shape[0])


def _num(value, name: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f'{name}必须是数字')
    if not math.isfinite(f) or not 0 <= f <= 1:
        raise ValueError(f'{name}必须在0到1之间')
    return f


def validate_roi(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('掩膜参数应为对象')
    mode = payload.get('mode')
    try:
        inset = int(payload.get('inset_px', 0))
    except (TypeError, ValueError):
        raise ValueError('内缩像素应为整数')
    if not 0 <= inset <= 150:
        raise ValueError('边缘内缩必须在0–150像素之间')
    result = {'mode': mode, 'inset_px': inset}
    if mode == 'ellipse':
        box = payload.get('box')
        if not isinstance(box, list) or len(box) != 4:
            raise ValueError('椭圆缺少两个对角点')
        x0, y0, x1, y1 = [_num(v, '椭圆坐标') for v in box]
        x0, x1 = sorted([x0, x1])
        y0, y1 = sorted([y0, y1])
        if (x1-x0) < .02 or (y1-y0) < .02:
            raise ValueError('椭圆范围过小；请重新拖拽')
        result['box'] = [x0, y0, x1, y1]
    elif mode == 'polygon':
        points = payload.get('points')
        if not isinstance(points, list) or not 3 <= len(points) <= 160:
            raise ValueError('多边形必须具有3–160个顶点')
        norm = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError('顶点必须包含[x,y]')
            norm.append([_num(point[0], '顶点X'), _num(point[1], '顶点Y')])
        arr = np.asarray(norm, dtype=np.float64)
        area = .5 * abs(np.dot(arr[:, 0], np.roll(arr[:, 1], 1)) -
                          np.dot(arr[:, 1], np.roll(arr[:, 0], 1)))
        if area < .0005:
            raise ValueError('多边形面积过小或顶点共线')
        result['points'] = norm
    else:
        raise ValueError('仅支持椭圆或多边形掩膜')
    return result


def make_mask(roi: dict, w: int, h: int) -> np.ndarray:
    if not 16 <= w <= 15000 or not 16 <= h <= 15000:
        raise ValueError('图片尺寸异常')
    mask = np.zeros((h, w), dtype=np.uint8)
    if roi['mode'] == 'ellipse':
        x0, y0, x1, y1 = roi['box']
        cx = round((x0+x1)/2*(w-1))
        cy = round((y0+y1)/2*(h-1))
        rx = round((x1-x0)/2*(w-1))
        ry = round((y1-y0)/2*(h-1))
        cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 255, -1)
    else:
        pts = np.asarray([[round(x*(w-1)), round(y*(h-1))]
                          for x, y in roi['points']], dtype=np.int32)
        cv2.fillPoly(mask, [pts], 255)
    inset = roi.get('inset_px', 0)
    if inset:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2*inset+1, 2*inset+1))
        mask = cv2.erode(mask, k)
    fraction = np.count_nonzero(mask) / (w*h)
    if fraction < .0005:
        raise ValueError('掩膜为空或太小，请减少内缩量')
    if fraction > .98:
        raise ValueError('掩膜几乎覆盖整张照片，请检查选区')
    return mask


def save_mask(base: Path, payload: dict) -> dict:
    roi = validate_roi(payload)
    w, h = raw_image_shape(base / 'raw')
    mask = make_mask(roi, w, h)
    img_save(base / 'roi_mask.png', mask)
    meta = {**roi, 'image_width': w, 'image_height': h,
            'pixels': int(np.count_nonzero(mask)),
            'coverage': float(np.count_nonzero(mask)/(w*h))}
    from .store import save_json
    save_json(base / 'roi.json', meta)
    return meta
