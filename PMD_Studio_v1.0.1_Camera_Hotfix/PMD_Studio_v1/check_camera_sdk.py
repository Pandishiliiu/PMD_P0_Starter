from pathlib import Path
import sys
import os
import importlib.util

print('Python executable:', sys.executable)
print('Python version:', sys.version.split()[0])
print('Studio path:', Path(__file__).resolve().parent)
print('PMD_GXIPY_DIR:', os.environ.get('PMD_GXIPY_DIR', '(not set)'))
print('PYTHONPATH:', os.environ.get('PYTHONPATH','(not set)'))

from studio.camera import _gxipy_roots, _load_gxipy
found = []
for root in _gxipy_roots():
    for src in (root/'gxipy'/'__init__.py', root/'gxipy.py'):
        if src.is_file(): found.append(str(src))
print('SDK source candidates:', found or '(none)')
try:
    gx = _load_gxipy()
    print('GXIPY_IMPORT_OK:', getattr(gx, '__file__', 'unknown location'))
    print('Next: restart start.bat, refresh /studio, connect the camera.')
except Exception as ex:
    print('GXIPY_IMPORT_FAILED:', type(ex).__name__, str(ex))
    print('Locate the folder containing gxipy\__init__.py from your previous working PMD scripts.')
    print('Set PMD_GXIPY_DIR to the parent of that gxipy folder, then restart start.bat.')
