import io
import os
import sys
import time
import tempfile
import unittest
from pathlib import Path
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['PMD_DEMO']='1'
from fastapi.testclient import TestClient
from studio.app import app
from studio import store

class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)

    def test_pages(self):
        self.assertEqual(self.client.get('/studio').status_code,200)
        self.assertIn('PMD Studio',self.client.get('/studio').text)
        self.assertEqual(self.client.get('/').status_code,200)
        self.assertEqual(self.client.get('/api/system').status_code,200)

    def test_planar_geometry_calibration(self):
        from studio.processing import decode,calibrate_screen,img_save
        w,h=960,720
        fx=fy=1700.
        W,H=227.06,157.79
        xx,yy=np.meshgrid(np.arange(w),np.arange(h))
        U=((xx-480)*600/fx+100)/W
        V=((yy-360)*600/fy+70)/H
        visible=(((xx-480)/350)**2+((yy-360)/295)**2<1)&(U>0)&(U<1)&(V>0)&(V<1)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);(p/'flat').mkdir()
            def save(name,values):
                img_save(p/'flat'/f'{name}.png',np.where(visible,values,15).clip(0,255).astype('uint8'))
            save('black',np.zeros((h,w)))
            save('white',np.full((h,w),240))
            for axis in ('x','y'):
                for freq in (1,4):
                    for phase in range(4):
                        c=U if axis=='x' else V
                        save(f'{axis}_f{freq}_p{phase}',128+100*np.cos(2*np.pi*(freq*c+phase/4)))
            q=decode(p/'flat',p/'decoded')
            self.assertGreater(q['valid_pixels'],10000)
            K=np.array([[fx,0,480],[0,fy,360],[0,0,1.]])
            np.savez(p/'intr.npz',K=K,dist=np.zeros(5))
            fit=calibrate_screen(p/'decoded'/'screen_uv.npz',{'n':[0,0,1],'d':-350},
                p/'intr.npz',p/'geometry.npz',W,H)
            self.assertLess(fit['median_reprojection_px'],.4)
            with np.load(p/'geometry.npz') as g:
                self.assertLess(np.linalg.norm(g['screen_origin']-[-100,-70,100]),1)

    def test_full_demo_measurement(self):
        c=self.client
        cal=c.post('/api/calibrations',json={'name':'Smoke geometry'}).json()
        self.assertIn('id',cal)
        K=np.array([[1700.,0,480],[0,1700,360],[0,0,1]])
        with tempfile.TemporaryDirectory() as td:
            geom=Path(td)/'geometry.npz'
            np.savez_compressed(geom,K=K,dist=np.zeros(5),mirror_n=np.array([0.,0.,1.]),
              mirror_d=-350.,screen_origin=np.array([-100.,-65.,100.]),
              screen_ex=np.array([1.,0.,0.]),screen_ey=np.array([0.,1.,0.]),
              screen_width_mm=227.06,screen_height_mm=157.79)
            with geom.open('rb') as f:
                imported=c.post(f'/api/calibrations/{cal["id"]}/geometry/import',files={'file':('geom.npz',f,'application/octet-stream')})
        self.assertEqual(imported.status_code,200,imported.text)
        self.assertTrue(imported.json()['ready'])
        run=c.post('/api/measurements',json={'name':'P0 demo run','calibration_id':cal['id']}).json()
        self.assertIn('id',run)
        self.assertEqual(c.post('/api/camera/connect').status_code,200)
        capture=c.post(f'/api/measurements/{run["id"]}/capture')
        self.assertEqual(capture.status_code,200,capture.text)
        self._wait(capture.json()['id'])
        decoded=c.post(f'/api/measurements/{run["id"]}/decode')
        self.assertEqual(decoded.status_code,200,decoded.text)
        r=self._wait(decoded.json()['id'])
        self.assertGreater(r['result']['valid_pixels'],1000)
        reconstructed=c.post(f'/api/measurements/{run["id"]}/reconstruct',json={'anchor_mm':0})
        self.assertEqual(reconstructed.status_code,200,reconstructed.text)
        r=self._wait(reconstructed.json()['id'])
        self.assertGreater(r['result']['points'],500)
        self.assertEqual(c.get(f'/api/measurements/{run["id"]}/file/reconstruction/height_detrended.png').status_code,200)
        self.assertEqual(c.get(f'/api/measurements/{run["id"]}/file/reconstruction/shape.npz').status_code,200)
        print('DEMO finished:',run['id'],r['result']['points'],'points')

    def _wait(self,id,limit=75):
        start=time.monotonic()
        while time.monotonic()-start<limit:
            x=self.client.get('/api/jobs/'+id).json()
            if x['status']=='done':return x
            if x['status']=='error':self.fail(x['error'])
            time.sleep(.2)
        self.fail('job timeout')

if __name__=='__main__':unittest.main(verbosity=2)
