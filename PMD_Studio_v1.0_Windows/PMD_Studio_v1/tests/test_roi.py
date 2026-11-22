"""Manual region test: geometry, persistence, phase intersection, invalidation."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['PMD_DEMO'] = '1'
from fastapi.testclient import TestClient
from studio.app import app
from studio import store
from studio.roi import make_mask, validate_roi
from studio.processing import decode, img_save


class ManualMaskTests(unittest.TestCase):
    def test_geometry(self):
        shape = validate_roi({'mode':'ellipse','box':[.1,.2,.9,.8], 'inset_px':12})
        mask = make_mask(shape, 480, 360)
        self.assertEqual(mask.shape, (360, 480))
        self.assertEqual(mask[0, 0], 0)
        self.assertEqual(mask[180, 240], 255)
        self.assertEqual(mask[180, 50], 0)
        poly = validate_roi({'mode':'polygon','points':[[.1,.1],[.9,.1],[.5,.9]]})
        m2 = make_mask(poly,480,360)
        self.assertEqual(m2[170, 240], 255)
        self.assertEqual(m2[330,240], 0)
        with self.assertRaises(ValueError):
            validate_roi({'mode':'ellipse','box':[0,0,.01,.5]})

    def test_api_mask_changes_decoder_output_and_archives(self):
        run = store.create('measurements', 'roi_patch_test',
                           {'captured':True, 'decoded':False, 'reconstructed':False,
                            'roi':None, 'source':'synthetic'})
        ident = run['id']
        root = store.entity_path('measurements', ident)
        raw = root / 'raw'
        raw.mkdir()
        try:
            h, w=360, 480
            yy,xx=np.mgrid[:h,:w]
            U=.20+.60*xx/w
            V=.17+.65*yy/h
            def save(name, val):
                img_save(raw / f'{name}.png', np.clip(val,0,255).astype(np.uint8))
            save('black',np.zeros((h,w)))
            save('white',np.full((h,w),230))
            for axis in ('x','y'):
                z=U if axis=='x' else V
                for freq in (1,4):
                    for phase in range(4):
                        save(f'{axis}_f{freq}_p{phase}',128+95*np.cos(2*np.pi*(freq*z+phase/4)))
            c=TestClient(app)
            endpoint=f'/api/measurements/{ident}/roi'
            self.assertEqual(c.get(endpoint).json()['width'], w)
            self.assertEqual(c.get(endpoint+'/preview').status_code,200)
            inp={'mode':'ellipse','box':[.13,.13,.87,.87],'inset_px':8}
            r=c.post(endpoint,json=inp)
            self.assertEqual(r.status_code,200,r.text)
            self.assertTrue((root/'roi_mask.png').exists())
            self.assertGreater(r.json()['roi']['pixels'], 5000)
            result=decode(raw,root/'decoded',manual_mask_path=root/'roi_mask.png')
            self.assertTrue(result['manual_roi_applied'])
            with np.load(root/'decoded/screen_uv.npz') as arr:
                phase_mask=arr['mask'].astype(bool)
            manual=(__import__('cv2').imdecode(np.fromfile(str(root/'roi_mask.png'),dtype='uint8'),0)>0)[::4,::4]
            self.assertFalse((phase_mask & ~manual).any())
            self.assertGreater(phase_mask.sum(), 1000)
            self.assertTrue((root/'decoded').exists())
            result=c.post(endpoint,json={'mode':'polygon','points':[[.2,.2],[.8,.2],[.8,.8],[.2,.8]]})
            self.assertEqual(result.status_code,200,result.text)
            self.assertFalse((root/'decoded').exists())
            self.assertTrue(any((root/'history').glob('*/decoded')))
            self.assertEqual(store.get('measurements',ident)['decoded'],False)
            result=c.post(endpoint+'/reset')
            self.assertEqual(result.status_code,200,result.text)
            self.assertFalse((root/'roi_mask.png').exists())
        finally:
            shutil.rmtree(root,ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
