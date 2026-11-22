"""Standalone camera-intrinsics calibration for a PMD Studio calibration record.

This module does not change reference-plane or screen-pose calibration. All
observations are in unmodified *native camera pixel* coordinates. Raw input
photographs are never overwritten; accepted/rejected observations are recorded.
"""
from __future__ import annotations

from pathlib import Path
import math
import cv2
import numpy as np

IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff'}
BOARD_TYPES = {'checkerboard', 'symmetric_circles', 'asymmetric_circles'}


def validate_board(config):
    if not isinstance(config, dict):
        raise ValueError('请提供标定板参数')
    typ = str(config.get('type', 'checkerboard'))
    if typ not in BOARD_TYPES:
        raise ValueError('板型只支持棋盘格、对称圆点阵、非对称圆点阵；Charuco请勿作为普通棋盘格处理')
    cols, rows = int(config.get('cols', 0)), int(config.get('rows', 0))
    pitch = float(config.get('pitch_mm', 0))
    if not 3 <= cols <= 32 or not 3 <= rows <= 32 or cols * rows > 600:
        raise ValueError('横、纵特征点数应为3–32，最多600点')
    if not math.isfinite(pitch) or not 0.01 <= pitch <= 1000:
        raise ValueError('相邻特征点间距(mm)无效')
    return {'type': typ, 'cols': cols, 'rows': rows, 'pitch_mm': pitch}


def board_object_points(config):
    typ, cols, rows, pitch = (config[k] for k in ('type', 'cols', 'rows', 'pitch_mm'))
    yy, xx = np.mgrid[0:rows, 0:cols].astype(np.float32)
    pts = np.zeros((rows * cols, 3), np.float32)
    if typ == 'asymmetric_circles':
        # OpenCV convention: x=(2*col + row parity)*pitch, y=row*pitch.
        pts[:, :2] = np.column_stack([((2 * xx + (yy.astype(np.int32) % 2)) * pitch).ravel(),
                                     (yy * pitch).ravel()])
    else:
        pts[:, :2] = np.column_stack([(xx * pitch).ravel(), (yy * pitch).ravel()])
    return pts


def _detect(gray, config):
    h, w = gray.shape
    # Detect on a reduced image; lift to full-native sensor coordinates before
    # camera calibration. This avoids very slow 12MP board detection.
    scale = min(1., 1800. / max(h, w))
    if scale < 1.:
        small = cv2.resize(gray, (round(w * scale), round(h * scale)),
                           interpolation=cv2.INTER_AREA)
    else:
        small = gray
    size = (config['cols'], config['rows'])
    typ = config['type']
    found, corners = False, None
    if typ == 'checkerboard':
        if hasattr(cv2, 'findChessboardCornersSB'):
            try:
                found, corners = cv2.findChessboardCornersSB(
                    small, size, cv2.CALIB_CB_NORMALIZE_IMAGE | cv2.CALIB_CB_EXHAUSTIVE)
            except cv2.error:
                found = False
        if not found:
            found, corners = cv2.findChessboardCorners(
                small, size, cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE)
    else:
        blob_params = cv2.SimpleBlobDetector_Params()
        blob_params.filterByArea = True
        blob_params.minArea = 10
        blob_params.maxArea = max(1200, int(small.size * .035))
        blob_params.filterByCircularity = True
        blob_params.minCircularity = .45
        blob_params.filterByConvexity = False
        blob_params.filterByInertia = False
        detector = cv2.SimpleBlobDetector_create(blob_params)
        flag = (cv2.CALIB_CB_ASYMMETRIC_GRID if typ == 'asymmetric_circles'
                else cv2.CALIB_CB_SYMMETRIC_GRID)
        found, corners = cv2.findCirclesGrid(small, size, flags=flag, blobDetector=detector)
    if not found or corners is None:
        return None
    corners = (corners.reshape(-1, 2).astype(np.float32) / scale)
    if typ == 'checkerboard':
        try:
            cv2.cornerSubPix(gray, corners.reshape(-1, 1, 2), (5, 5), (-1, -1),
                (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 35, .005))
        except cv2.error:
            pass
    if len(corners) != config['cols'] * config['rows']:
        return None
    if not np.isfinite(corners).all():
        return None
    return corners.reshape(-1, 1, 2)


def _calibrate(objs, imgs, image_size, fix_k3=True):
    flags = cv2.CALIB_FIX_K3 if fix_k3 else 0
    rms, K, D, rvecs, tvecs = cv2.calibrateCamera(
        objs, imgs, image_size, None, None, flags=flags,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_MAX_ITER, 100, 1e-9))
    if not np.isfinite(K).all() or not np.isfinite(D).all() or not np.isfinite(rms):
        raise RuntimeError('相机标定计算结果非有限值；检查角点、焦距/对焦与采样姿态')
    per_view = []
    for obj, img, rv, tv in zip(objs, imgs, rvecs, tvecs):
        predicted, _ = cv2.projectPoints(obj, rv, tv, K, D)
        dif = predicted.reshape(-1, 2) - img.reshape(-1, 2)
        per_view.append(float(np.sqrt(np.mean(np.sum(dif * dif, axis=1)))))
    return float(rms), K, D, rvecs, tvecs, np.asarray(per_view)


def _quality(K, D, rms, used_corners, rvecs, image_size):
    w, h = image_size
    centers = np.stack([z.reshape(-1, 2).mean(axis=0) for z in used_corners])
    areas = []
    for z in used_corners:
        hull = cv2.convexHull(z.reshape(-1, 2).astype('float32'))
        areas.append(abs(float(cv2.contourArea(hull))) / (w*h))
    view_normals = []
    for rv in rvecs:
        rot, _ = cv2.Rodrigues(rv)
        n = rot[:, 2].copy()
        if n[2] < 0: n = -n
        view_normals.append(n)
    angular_spread = 0.
    for i in range(len(view_normals)):
        for j in range(i):
            angular_spread = max(angular_spread, math.degrees(math.acos(
                float(np.clip(np.dot(view_normals[i], view_normals[j]), -1, 1)))))
    spread_x = float(np.ptp(centers[:, 0]) / w)
    spread_y = float(np.ptp(centers[:, 1]) / h)
    warnings = []
    if len(used_corners) < 12: warnings.append('有效照片不足12张：建议18–25张多姿态照片')
    if angular_spread < 20.: warnings.append('棋盘倾角变化不足20°：焦距与畸变可能耦合')
    if min(spread_x, spread_y) < .15: warnings.append('棋盘中心覆盖范围较窄：请移动到画面边缘和角落')
    if max(areas) < .06: warnings.append('标定板在画面中占比较小：内参约束可能很弱')
    if rms > 1.: warnings.append('重投影RMS大于1 px：建议检查清晰度、光照与角点检测')
    if not (.7 < K[0,0] / K[1,1] < 1.3):
        warnings.append('fx/fy相差较大：可能存在标定板方向不足或几何退化')
    if not (0 <= K[0,2] <= w and 0 <= K[1,2] <= h):
        warnings.append('主点落在图像外：当前内参可能不可信')
    if max(abs(float(v)) for v in D.ravel()) > 100:
        warnings.append('畸变系数出现异常大值：重拍更多倾斜、边缘姿态')
    return {'center_spread_fraction': [spread_x, spread_y],
            'max_board_area_fraction': float(max(areas)),
            'tilt_range_deg': float(angular_spread),
            'warnings': warnings}


def solve_intrinsics(folder, output_npz, board, fix_k3=True, *, min_views=8):
    """Detect, calibrate, conservatively reject a bad view, and save report.

    Board profile is the physical *camera-intrinsic board*, independent of the
    board subsequently used to place the planar reference mirror.
    """
    from .store import save_json
    config = validate_board(board)
    folder = Path(folder)
    files = sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT)
    if len(files) < min_views:
        raise ValueError(f'内参标定至少需{min_views}张图像；当前只有{len(files)}张')
    obj = board_object_points(config)
    image_size = None
    valid = []
    log = []
    out = Path(output_npz)
    annotations = out.parent / 'intrinsic_corners'
    annotations.mkdir(parents=True, exist_ok=True)
    for k, path in enumerate(files):
        im = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if im is None:
            log.append({'file': path.name, 'detected': False, 'reason': '无法读取图像'})
            continue
        shape = (im.shape[1], im.shape[0])
        if image_size is None: image_size = shape
        if image_size != shape:
            log.append({'file': path.name, 'detected': False, 'reason': f'尺寸不匹配 {shape}，预期 {image_size}'})
            continue
        corners = _detect(im, config)
        if corners is None:
            log.append({'file': path.name, 'detected': False, 'reason': '角点/圆点识别失败'})
            continue
        # Detection preview is capped for quick examination.
        preview_scale = min(1., 1250 / max(shape))
        preview = cv2.cvtColor(im, cv2.COLOR_GRAY2BGR)
        cv2.drawChessboardCorners(preview, (config['cols'], config['rows']), corners, True)
        if preview_scale < 1:
            preview = cv2.resize(preview, None, fx=preview_scale, fy=preview_scale,
                interpolation=cv2.INTER_AREA)
        success, buf = cv2.imencode('.jpg', preview, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if success: buf.tofile(str(annotations / f'{path.stem}.jpg'))
        valid.append((path.name, corners))
        log.append({'file': path.name, 'detected': True})
    if len(valid) < min_views:
        raise ValueError(f'成功检测特征点只有{len(valid)}张（至少{min_views}张）；检查板型、特征点行列和图像清晰度')
    chosen = list(range(len(valid)))
    def solve(chosen):
        return _calibrate([obj.copy() for _ in chosen], [valid[i][1] for i in chosen],
                          image_size, fix_k3=fix_k3)
    rms, K, D, rvecs, tvecs, view_errors = solve(chosen)
    # Conservative single-pass exclusion: remove a clear outlier only when the
    # remaining 8+ observations still yield a plausible camera solution.
    if len(chosen) >= min_views + 2:
        med = float(np.median(view_errors))
        bad = np.flatnonzero(view_errors > max(1.5, med*2.5))
        if 0 < len(bad) <= max(1, int(len(chosen) * .2)) and len(chosen) - len(bad) >= min_views:
            proposed = [k for ii, k in enumerate(chosen) if ii not in set(bad)]
            try:
                rms2, K2, D2, rvecs2, tvecs2, errors2 = solve(proposed)
                if rms2 < rms:
                    chosen = proposed
                    rms, K, D, rvecs, tvecs, view_errors = rms2, K2, D2, rvecs2, tvecs2, errors2
            except cv2.error:
                pass
    chosen_names = {valid[i][0] for i in chosen}
    used_map = {valid[k][0]: float(err) for k, err in zip(chosen, view_errors)}
    for entry in log:
        if entry['detected']:
            entry['accepted'] = entry['file'] in chosen_names
            entry['rms_px'] = used_map.get(entry['file'])
            if not entry['accepted']: entry['reason'] = '高重投影残差，已从拟合中排除'
    report = _quality(K, D, rms, [valid[i][1] for i in chosen], rvecs, image_size)
    summary = {'board': config, 'image_size': list(image_size),
               'num_photos': len(files), 'num_detected': len(valid),
               'num_used': len(chosen), 'rms_px': rms,
               'fx': float(K[0, 0]), 'fy': float(K[1, 1]),
               'cx': float(K[0, 2]), 'cy': float(K[1, 2]),
               'dist': [float(v) for v in D.ravel()],
               'fix_k3': bool(fix_k3),
               'quality': report, 'photos': log}
    if not np.isfinite(rms) or not np.isfinite(K).all() or not np.isfinite(D).all():
        raise RuntimeError('计算结果异常，拒绝保存内参')
    out.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write via unique temp path; no partial NPZ when interrupted.
    tmp = out.with_name(out.stem + '.tmp.npz')
    np.savez_compressed(tmp, K=K, dist=D.reshape(-1),
                        image_width=image_size[0], image_height=image_size[1],
                        rms_px=rms)
    tmp.replace(out)
    save_json(out.parent / 'intrinsic_result.json', summary)
    return summary
