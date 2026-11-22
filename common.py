"""Shared pattern schedule for PMD P0. 4-step cosines at 1/4/16 cycles across screen."""
import json
from pathlib import Path

PATTERNS = [{'name': 'black', 'axis': None, 'cycles': 0, 'phase': 0},
            {'name': 'white', 'axis': None, 'cycles': 0, 'phase': 0}]
for axis in ('x', 'y'):
    for cycles in (1, 4, 16):
        for step in range(4):
            PATTERNS.append({'name': f'{axis}_f{cycles}_p{step}',
                             'axis': axis, 'cycles': cycles, 'phase': step})
assert len(PATTERNS) == 26


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def imread_unicode(path, flags=None):
    """OpenCV read that supports non-ASCII Windows file paths."""
    import numpy as np
    import cv2
    if flags is None:
        flags = cv2.IMREAD_UNCHANGED
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), flags)


def imwrite_unicode(path, image):
    """OpenCV write that supports non-ASCII Windows file paths."""
    import cv2
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    ok, data = cv2.imencode(target.suffix, image)
    if not ok:
        return False
    data.tofile(str(target))
    return True
