"""Filesystem-backed, immutable-snapshot measurement data management."""
from __future__ import annotations
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / 'data'
CAL_ROOT = DATA / 'calibrations'
RUN_ROOT = DATA / 'measurements'
TEST_ROOT = DATA / 'test_shots'
for p in [DATA, CAL_ROOT, RUN_ROOT, TEST_ROOT]:
    p.mkdir(parents=True, exist_ok=True)

ID_RE = re.compile(r'^[a-z0-9_-]{6,64}$')

def uid(prefix):
    return f'{prefix}_{datetime.now().strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}'

def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')

def entity_path(kind: str, ident: str):
    if kind not in ('calibrations', 'measurements') or not ID_RE.fullmatch(ident):
        raise ValueError('Invalid data identifier')
    root = CAL_ROOT if kind == 'calibrations' else RUN_ROOT
    p = root / ident
    if not p.is_dir():
        raise FileNotFoundError(f'{kind}: {ident}')
    return p

def record_path(path):
    return path / 'record.json'

def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def create(kind, name, extras=None):
    if not name.strip():
        raise ValueError('Name required')
    ident = uid('cal' if kind == 'calibrations' else 'run')
    root = CAL_ROOT if kind == 'calibrations' else RUN_ROOT
    p = root / ident
    p.mkdir(parents=True)
    doc = {'id': ident, 'name': name.strip()[:100], 'created_at': now(), 'kind': kind,
           'status': 'created', **(extras or {})}
    save_json(record_path(p), doc)
    return doc

def update(kind, ident, **kwargs):
    p = entity_path(kind, ident)
    doc = read_json(record_path(p))
    doc.update(kwargs)
    doc['updated_at'] = now()
    save_json(record_path(p), doc)
    return doc

def get(kind, ident):
    return read_json(record_path(entity_path(kind, ident)))

def list_records(kind):
    root = CAL_ROOT if kind == 'calibrations' else RUN_ROOT
    docs = []
    for f in root.glob('*/record.json'):
        try:
            docs.append(read_json(f))
        except Exception:
            continue
    return sorted(docs, key=lambda d: d.get('created_at', ''), reverse=True)

def clone_geometry(cal_id, run_id):
    src = entity_path('calibrations', cal_id) / 'geometry.npz'
    if not src.is_file():
        raise ValueError('Calibration is not ready')
    dst = entity_path('measurements', run_id) / 'calibration_snapshot'
    dst.mkdir(exist_ok=True)
    shutil.copy2(src, dst / 'geometry.npz')
    cal = get('calibrations', cal_id)
    save_json(dst / 'source.json', {'calibration_id': cal_id,
        'calibration_name': cal['name'], 'captured_at': now(),
        'screen_width_mm': cal.get('screen_width_mm'),
        'screen_height_mm': cal.get('screen_height_mm'),
        'screen_pixels': cal.get('screen_pixels')})
    return dst / 'geometry.npz'

# Fixed layout: user IDs are never used as arbitrary filesystem paths.
def data_file(kind, ident, filename):
    base = entity_path(kind, ident)
    path = (base / filename).resolve()
    if not path.is_relative_to(base.resolve()):
        raise ValueError('Invalid file path')
    return path
