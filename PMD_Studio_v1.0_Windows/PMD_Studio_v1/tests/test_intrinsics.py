"""Repeatable on-disk fake checkerboard images and endpoint tests; no hardware."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
import cv2
import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['PMD_DEMO']='1'
from studio.intrinsics import solve_intrinsics, validate_board, board_object_points
from studio.processing import read_intrinsics
from fastapi.testclient import TestClient
from studio.app import app
from studio import store


def make_board_images(folder, count=12):
    """Render known checkerboard using a virtual camera's projectPoints."""
    folder.mkdir(parents=True, exist_ok=True)
    nx, ny, pitch = 9, 6, 20.
    cell = 80
    ww, hh = (nx + 1) * cell, (ny + 1) * cell
    texture = np.full((hh, ww), 220, np.uint8)
    for j in range(ny + 1):
        for i in range(nx + 1):
            if (i+j) % 2:
                texture[j*cell:(j+1)*cell, i*cell:(i+1)*cell]=35
    K = np.array([[1250.,0,500.],[0,1270.,370.],[0,0,1]])
    source = np.array([[0,0],[ww,0],[ww,hh],[0,hh]],np.float32)
    boundary = np.array([[-pitch,-pitch,0], [nx*pitch,-pitch,0],
                         [nx*pitch,ny*pitch,0],[-pitch,ny*pitch,0]], np.float32)
    for idx in range(count):
        ax, ay = (.11+.24*np.sin(idx*1.9)), (.14+.26*np.cos(idx*1.7))
        rz = .20*np.sin(idx*.5)
        rvec = np.array([ax,ay,rz],dtype=np.float64)
        tvec = np.array([22*np.cos(idx*2.1)-60, 16*np.sin(idx*1.3)-40, 330+18*np.cos(idx)])
        projected,_ = cv2.projectPoints(boundary,rvec,tvec,K,None)
        M = cv2.getPerspectiveTransform(source, projected.reshape(4,2).astype(np.float32))
        background = np.full((740,1000), 128,np.uint8)
        warped = cv2.warpPerspective(texture,M,(1000,740),
                                    flags=cv2.INTER_LINEAR,borderValue=128)
        # Calibration board has enough contrast even when partially shadowed.
        image = cv2.GaussianBlur(warped,(3,3),.45)
        ok,buf = cv2.imencode('.png',image)
        assert ok
        buf.tofile(str(folder / f'intr_{idx+1:03d}.png'))


class IntrinsicsTest(unittest.TestCase):
    def test_board_geometry(self):
        p=board_object_points(validate_board({'type':'checkerboard','cols':9,'rows':6,'pitch_mm':20}))
        self.assertEqual(p.shape,(54,3))
        self.assertEqual(p[0].tolist(),[0,0,0])
        self.assertEqual(p[1].tolist(),[20,0,0])
        self.assertEqual(p[9].tolist(),[0,20,0])

    def test_solver_and_npz_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'intrinsic_images'
            make_board_images(folder)
            summary=solve_intrinsics(folder,Path(tmp)/'intrinsics.npz',
                {'type':'checkerboard','cols':9,'rows':6,'pitch_mm':20.},min_views=8)
            print('SOLVER',summary['num_used'],summary['rms_px'],summary['fx'],summary['fy'])
            self.assertGreaterEqual(summary['num_used'],8)
            self.assertLess(summary['rms_px'],1.8)
            K,dist=read_intrinsics(Path(tmp)/'intrinsics.npz')
            self.assertEqual(K.shape,(3,3))
            self.assertEqual(len(dist),5)
            self.assertTrue((Path(tmp)/'intrinsic_result.json').exists())
            self.assertGreater(len(list((Path(tmp)/'intrinsic_corners').glob('*.jpg'))),8)

    def test_endpoints_and_locked_calibration(self):
        client=TestClient(app)
        rec=client.post('/api/calibrations',json={'name':'测试商用标定板'}).json()
        id=rec['id']
        # New camera-calibration feature must not touch planar images or geometry.
        folder=store.entity_path('calibrations',id)
        with tempfile.TemporaryDirectory() as tmp:
            make_board_images(Path(tmp)/'images',count=10)
            images=list((Path(tmp)/'images').glob('*.png'))
            with __import__('contextlib').ExitStack() as stack:
                files=[('files',(p.name,stack.enter_context(p.open('rb')),'image/png')) for p in images]
                resp=client.post(f'/api/calibrations/{id}/intrinsics/photos/upload', files=files)
            self.assertEqual(resp.status_code,200,resp.text)
            job=client.post(f'/api/calibrations/{id}/intrinsics/solve',json={
                'board':{'type':'checkerboard','cols':9,'rows':6,'pitch_mm':20},'fix_k3':True})
            self.assertEqual(job.status_code,200,job.text)
            import time
            for i in range(120):
                r=client.get('/api/jobs/'+job.json()['id']).json()
                if r['status'] != 'running': break
                time.sleep(.15)
            self.assertEqual(r['status'],'done',r)
            self.assertTrue(client.get(f'/api/calibrations/{id}').json()['intrinsics'])
            self.assertEqual(client.get(f'/api/calibrations/{id}/intrinsics/download').status_code,200)
            self.assertFalse((folder/'board').exists())
            self.assertFalse((folder/'flat').exists())
            self.assertFalse((folder/'geometry.npz').exists())

if __name__=='__main__':
    unittest.main(verbosity=2)
