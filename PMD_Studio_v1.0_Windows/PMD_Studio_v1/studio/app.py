"""PMD Studio v1.0 - local HTTP control and data management.
Run one uvicorn worker only: gxipy camera must be single-owner.
"""
from __future__ import annotations
import io
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from fastapi import FastAPI, File, UploadFile, Body, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, Response, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from .camera import camera
from .patterns import patterns
from . import store
from .processing import read_intrinsics, board_plane, decode, calibrate_screen, reconstruct
from . import roi as roi_tools
from .curvature import analyze_shape
from .intrinsics import solve_intrinsics, validate_board, IMAGE_EXT

HERE = Path(__file__).resolve().parent.parent
WEB = HERE / 'web'
app = FastAPI(title='PMD Studio',docs_url=None,redoc_url=None)
app.mount('/assets',StaticFiles(directory=str(WEB)),name='assets')

# Only the iPad's pattern page/API are available over the LAN; the full
# control and data API is localhost-only. The program is not a public server.
@app.middleware('http')
async def local_control_only(request,call_next):
    remote=request.client.host if request.client else ''
    ipad_routes={'/','/ipad','/api/state','/api/ack','/api/status'}
    if remote not in ('127.0.0.1','::1','testclient') and request.url.path not in ipad_routes:
        return JSONResponse({'detail':'仅允许本机访问控制界面'},status_code=403)
    return await call_next(request)
JOBS:dict[str,dict[str,Any]]={}
JOB_LOCK=threading.RLock()

@app.exception_handler(ValueError)
async def value_error(req,exc): return JSONResponse({'detail':str(exc)},status_code=400)
@app.exception_handler(FileNotFoundError)
async def file_error(req,exc): return JSONResponse({'detail':str(exc)},status_code=404)
@app.exception_handler(RuntimeError)
async def runtime_error(req,exc): return JSONResponse({'detail':str(exc)},status_code=409)

@app.get('/',response_class=HTMLResponse)
@app.get('/ipad',response_class=HTMLResponse)
def ipad_page():return (WEB/'ipad.html').read_text(encoding='utf-8')
@app.get('/studio',response_class=HTMLResponse)
def index():return (WEB/'index.html').read_text(encoding='utf-8')

# Compatibility with the already-used /api/set, /api/status, /api/state, /api/ack
@app.get('/api/state')
def ipad_state():
    patterns.heartbeat()
    return patterns.state()
@app.post('/api/ack')
def ipad_ack(payload:dict=Body(...)):return patterns.acknowledge(payload)
@app.get('/api/status')
def ipad_status():return patterns.status()
@app.get('/api/set')
def ipad_set(index:int):return patterns.select(index)
@app.post('/api/patterns/select')
def set_pattern(payload:dict=Body(...)):
    ensure_not_capturing()
    return patterns.select(int(payload['index']))
@app.post('/api/patterns/config')
def set_pattern_config(payload:dict=Body(...)):
    ensure_not_capturing()
    return patterns.configure(payload)
@app.get('/api/system')
def system():return {'camera':camera.status(),'ipad':patterns.status(),
         'demo':camera.demo,'data_directory':str(store.DATA.resolve())}

@app.post('/api/camera/connect')
def connect():return camera.connect()
@app.post('/api/camera/disconnect')
def disconnect():
    ensure_not_capturing()
    return camera.disconnect()
@app.post('/api/camera/exposure')
def exposure(payload:dict=Body(...)):
    ensure_not_capturing()
    return camera.set_exposure(payload.get('us',10000))
@app.post('/api/camera/preview')
def preview(payload:dict=Body(...)):
    if payload.get('on'):return camera.start_preview()
    camera.stop_preview()
    return camera.status()
@app.get('/api/camera/frame')
def camera_frame():
    if camera.frame_jpeg is None:return Response(status_code=204)
    return Response(camera.frame_jpeg,media_type='image/jpeg',headers={'Cache-Control':'no-store'})
@app.get('/api/camera/stream')
def camera_stream():
    """Latest-frame MJPEG stream. One GET replaces 2 Hz JPEG polling."""
    if not camera.previewing:
        return Response(status_code=204)

    def frames():
        seq = camera.frame_seq
        while camera.previewing:
            next_frame = camera.wait_preview_frame(seq, timeout=1.0)
            if next_frame is None:
                continue
            seq, jpg = next_frame
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n'
                   b'Content-Length: ' + str(len(jpg)).encode('ascii')
                   + b'\r\n\r\n' + jpg + b'\r\n')

    return StreamingResponse(
        frames(),
        media_type='multipart/x-mixed-replace; boundary=frame',
        headers={'Cache-Control':'no-store, no-cache, must-revalidate',
                 'Pragma':'no-cache',
                 'X-Accel-Buffering':'no'},
    )

@app.post('/api/camera/snapshot')
def camera_snapshot():
    ensure_not_capturing()
    frame=camera.shoot()
    filename=f'test_{store.uid("shot")}.png'
    info=camera.write_png(frame,store.TEST_ROOT/filename)
    return {'info':info,'file':filename}
@app.get('/api/camera/snapshot/{filename}')
def snapshot_file(filename:str):
    if not filename.startswith('test_shot_') or not filename.endswith('.png') or '/' in filename:
        raise ValueError('Invalid image name')
    path=store.TEST_ROOT/filename
    if not path.is_file():raise FileNotFoundError(filename)
    return FileResponse(path,media_type='image/png')

@app.get('/api/calibrations')
def cals():return store.list_records('calibrations')
@app.post('/api/calibrations')
def new_cal(payload:dict=Body(...)):
    cfg=patterns.status()['config']
    return store.create('calibrations',payload.get('name',''),{
        'board_inner':payload.get('board_inner',[9,6]),
        'square_mm':float(payload.get('square_mm',4.0)),
        'screen_width_mm':float(payload.get('screen_width_mm',cfg['screen_width_mm'])),
        'screen_height_mm':float(payload.get('screen_height_mm',cfg['screen_height_mm'])),
        'screen_pixels':[cfg['expected_width'],cfg['expected_height']],
        'plane':None,'geometry_quality':None,'ready':False})
@app.get('/api/calibrations/{ident}')
def cal(ident:str):return store.get('calibrations',ident)

def mutable_cal(ident):
    rec=store.get('calibrations',ident)
    if rec.get('ready'):raise ValueError('标定已完成并锁定；请新建一个标定版本')
    return rec

# Camera-intrinsics calibration is separate from the fixed reference-plane
# board. Board tilt/translation is REQUIRED for this step; reference-plane
# images must remain at a single, fixed physical plane.
@app.get('/api/calibrations/{ident}/intrinsics/photos')
def cal_intrinsic_photos(ident:str):
    store.get('calibrations', ident)
    folder=store.entity_path('calibrations',ident)/'intrinsic_images'
    images=sorted(p.name for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXT) if folder.exists() else []
    return {'count':len(images),'files':images}

@app.post('/api/calibrations/{ident}/intrinsics/capture')
def cal_intrinsic_capture(ident:str):
    mutable_cal(ident)
    ensure_not_capturing()
    if not camera.status()['connected']:
        raise RuntimeError('请先连接相机')
    folder=store.entity_path('calibrations',ident)/'intrinsic_images'
    folder.mkdir(parents=True,exist_ok=True)
    used={p.stem for p in folder.iterdir() if p.is_file()}
    index=1
    while f'intr_{index:03d}' in used: index+=1
    path=folder/f'intr_{index:03d}.png'
    frame=camera.shoot()
    info=camera.write_png(frame,path)
    return {'file':path.name,'width':int(frame.shape[1]),'height':int(frame.shape[0]),'shot':info}

@app.post('/api/calibrations/{ident}/intrinsics/photos/upload')
async def cal_intrinsic_upload(ident:str,files:list[UploadFile]=File(...)):
    mutable_cal(ident)
    ensure_not_capturing()
    if not 1 <= len(files) <= 60:
        raise ValueError('每次最多上传60张图片')
    folder=store.entity_path('calibrations',ident)/'intrinsic_images'
    folder.mkdir(parents=True,exist_ok=True)
    existing={p.stem for p in folder.iterdir() if p.is_file()}
    index=1
    accepted=[]
    for f in files:
        suffix=Path(f.filename or '').suffix.lower()
        if suffix not in IMAGE_EXT: continue
        data=await f.read()
        if not 100 <= len(data) <= 45*1024*1024:
            raise ValueError('单张图片大小应在100B到45MB之间')
        # Do not trust the file extension alone. Files are never executed.
        import cv2
        import numpy as np
        arr=cv2.imdecode(np.frombuffer(data,dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
        if arr is None or min(arr.shape)<100:
            raise ValueError(f'无效图像：{f.filename}')
        while f'intr_{index:03d}' in existing:index+=1
        stem=f'intr_{index:03d}'
        existing.add(stem)
        path=folder/(stem+suffix)
        path.write_bytes(data)
        accepted.append({'file':path.name,'width':int(arr.shape[1]),'height':int(arr.shape[0])})
    if not accepted:raise ValueError('没有可用的图像')
    return {'saved':accepted,'total':len(list(folder.glob('intr_*')))}

@app.post('/api/calibrations/{ident}/intrinsics/solve')
def cal_intrinsic_solve(ident:str,payload:dict=Body(...)):
    rec=mutable_cal(ident)
    ensure_not_capturing()
    board=validate_board(payload.get('board',{}))
    fix_k3=bool(payload.get('fix_k3',True))
    base=store.entity_path('calibrations',ident)
    def work(progress):
        progress(.04,'读取图像、检测特征点')
        result=solve_intrinsics(base/'intrinsic_images',base/'intrinsics.npz',
                                board,fix_k3=fix_k3)
        progress(.95,'保存K、畸变系数和逐张残差')
        store.update('calibrations',ident,intrinsics=True,
                     intrinsic_quality=result)
        return result
    return new_job('相机内参标定：'+rec['name'],work)

@app.get('/api/calibrations/{ident}/intrinsics/download')
def cal_intrinsic_download(ident:str):
    store.get('calibrations',ident)
    path=store.entity_path('calibrations',ident)/'intrinsics.npz'
    if not path.is_file():raise FileNotFoundError('尚未计算或导入相机内参')
    return FileResponse(path,media_type='application/octet-stream',filename='camera_intrinsics.npz')

@app.get('/api/calibrations/{ident}/intrinsics/annotation/{name}')
def cal_intrinsic_annotation(ident:str,name:str):
    store.get('calibrations',ident)
    import re
    if not re.fullmatch(r'intr_\d{3}\.jpg',name):
        raise ValueError('非法预览文件名')
    path=store.entity_path('calibrations',ident)/'intrinsic_corners'/name
    if not path.is_file():raise FileNotFoundError(name)
    return FileResponse(path,media_type='image/jpeg')

@app.post('/api/calibrations/{ident}/intrinsics')
async def cal_intrinsics(ident:str,file:UploadFile=File(...)):
    mutable_cal(ident)
    if not file.filename.lower().endswith('.npz'):raise ValueError('请选择NPZ文件(K,dist)')
    p=store.entity_path('calibrations',ident)/'intrinsics.npz'
    payload=await file.read()
    if len(payload)>30*1024*1024:raise ValueError('内参文件过大')
    p.write_bytes(payload)
    try:K,dist=read_intrinsics(p)
    except Exception:
        p.unlink(missing_ok=True)
        raise
    store.update('calibrations',ident,intrinsics=True)
    return {'K':K.tolist(),'dist':dist.tolist()}

@app.post('/api/calibrations/{ident}/geometry/import')
async def import_geometry(ident:str,file:UploadFile=File(...)):
    mutable_cal(ident)
    if not file.filename.lower().endswith('.npz'):raise ValueError('请选择NPZ几何文件')
    data=await file.read()
    if len(data)>30*1024*1024:raise ValueError('文件过大')
    p=store.entity_path('calibrations',ident)/'geometry.npz'
    p.write_bytes(data)
    try:
        import numpy as np
        with np.load(p,allow_pickle=False) as g:
            required=('K','dist','mirror_n','mirror_d','screen_origin','screen_ex','screen_ey',
                      'screen_width_mm','screen_height_mm')
            for key in required: _=g[key]
            w=float(g['screen_width_mm']);h=float(g['screen_height_mm'])
            if not 10<w<1000 or not 10<h<1000:raise ValueError('屏幕物理尺寸无效')
        K,dist=read_intrinsics(p)
    except Exception:
        p.unlink(missing_ok=True)
        raise
    shutil.copy2(p,store.entity_path('calibrations',ident)/'intrinsics.npz')
    return store.update('calibrations',ident,ready=True,status='ready',intrinsics=True,
         screen_width_mm=w,screen_height_mm=h,geometry_quality={'mode':'imported','warning':'导入已有参数，准确性未验证'})

@app.post('/api/calibrations/{ident}/board/capture')
def capture_board(ident:str):
    mutable_cal(ident)
    if camera.acquiring:raise ValueError('采集中不能拍棋盘格')
    p=store.entity_path('calibrations',ident)/'board'
    p.mkdir(exist_ok=True)
    filename=f'board_{len(list(p.glob("board_*.png")))+1:02d}.png'
    info=camera.write_png(camera.shoot(),p/filename)
    return info

@app.post('/api/calibrations/{ident}/board/upload')
async def upload_board(ident:str,files:list[UploadFile]=File(...)):
    mutable_cal(ident)
    p=store.entity_path('calibrations',ident)/'board'
    p.mkdir(exist_ok=True)
    count=len(list(p.glob('board_*.png')))
    accepted=[]
    for f in files:
        if Path(f.filename).suffix.lower() not in ('.png','.jpg','.jpeg','.bmp'):
            continue
        payload=await f.read()
        if len(payload)>40*1024*1024:raise ValueError('单张图片过大')
        count+=1
        suffix=Path(f.filename).suffix.lower()
        dst=p/f'board_{count:02d}{suffix}'
        dst.write_bytes(payload)
        accepted.append(dst.name)
    return {'saved':accepted}

@app.post('/api/calibrations/{ident}/board/solve')
def solve_board(ident:str):
    rec=mutable_cal(ident)
    base=store.entity_path('calibrations',ident)
    intr=base/'intrinsics.npz'
    if not intr.is_file():raise ValueError('请先导入相机内参')
    summary=board_plane(base/'board',intr,rec['board_inner'],rec['square_mm'])
    store.save_json(base/'plane.json',summary)
    store.update('calibrations',ident,plane=summary)
    return summary

# Pattern capture or raw-file import into a single atomic data unit.
def ensure_not_capturing():
    if camera.acquiring:
        raise ValueError('自动采集中，暂时不能修改配置')

def safe_image_zip(blob:bytes,destination:Path,required=None):
    if len(blob)>900*1024*1024:raise ValueError('ZIP文件过大')
    destination.mkdir(parents=True,exist_ok=True)
    found=set()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        for item in z.infolist():
            if item.is_dir():continue
            name=Path(item.filename.replace('\\','/')).name
            if not name or not name.endswith('.png'):continue
            if name in found:raise ValueError(f'ZIP中存在重复文件：{name}')
            # Only known phase image names can be extracted.
            import re
            if not (name in ('black.png','white.png') or re.fullmatch(r'[xy]_f\d+_p[0-3]\.png',name)):
                continue
            if item.file_size>55*1024*1024:raise ValueError('图片过大')
            with z.open(item) as src,open(destination/name,'wb') as dst:
                shutil.copyfileobj(src,dst)
            found.add(name)
    needed={'black.png','white.png'}|{f'{ax}_f{f}_p{k}.png'
          for ax in ('x','y') for f in (1,4) for k in range(4)}
    missing=sorted(needed-found-set(p.name for p in destination.glob('*.png')))
    if missing:raise ValueError('缺少相移图：'+', '.join(missing[:5]))
    return sorted(found)

def new_job(label,fn):
    with JOB_LOCK:
        if any(j['status']=='running' for j in JOBS.values()):
            raise ValueError('已有任务正在运行')
        jobid=store.uid('job')
        job={'id':jobid,'label':label,'status':'running','progress':0.,'message':'启动中',
             'created_at':store.now(),'error':None,'result':None}
        JOBS[jobid]=job
    def progress(value,message):
        with JOB_LOCK:
            job['progress']=round(float(value),4)
            job['message']=str(message)
    def worker():
        try:
            result=fn(progress)
            with JOB_LOCK:
                job['status']='done';job['progress']=1.;job['message']='完成';job['result']=result
        except Exception as exc:
            with JOB_LOCK:
                job['status']='error';job['message']='失败';job['error']=f'{type(exc).__name__}: {exc}'
        finally:
            if len(JOBS)>100:
                # Never prune the most recently inserted job.
                oldest=list(JOBS)[:-50]
                for k in oldest:
                    if JOBS.get(k,{}).get('status')!='running':JOBS.pop(k,None)
    threading.Thread(target=worker,daemon=True,name=jobid).start()
    return job

@app.get('/api/jobs/{jobid}')
def job_status(jobid:str):
    if jobid not in JOBS:raise FileNotFoundError('Unknown job')
    return JOBS[jobid]

def capture_sequence(out:Path,progress,mode):
    if not camera.status()['connected']:raise RuntimeError('请先连接相机')
    if not camera.demo and not patterns.status()['connected']:
        raise RuntimeError('iPad未连接，请在iPad上打开 http://电脑IP:8000/')
    previous_preview=camera.previewing
    camera.acquiring=True
    camera.demo_mode=mode
    camera.stop_preview()
    pstatus=patterns.status();plan=pstatus['plan'];cfg=pstatus['config']
    if out.exists() and list(out.glob('*.png')):
        camera.acquiring=False
        if previous_preview:camera.start_preview()
        raise RuntimeError('原始采集目录已经包含图像，禁止覆盖。请创建新的数据单元。')
    out.mkdir(parents=True,exist_ok=True)
    shots=[]
    try:
        for idx,pat in enumerate(plan):
            revision=patterns.select(idx)['rev']
            if not camera.demo:
                patterns.wait_ack(revision,timeout=20)
                time.sleep(1.2)  # LCD settled AFTER acknowledgement; proven safe in project.
            frame=camera.shoot(pat,cfg)
            info=camera.write_png(frame,out/(pat['name']+'.png'))
            info['index']=idx;shots.append(info)
            progress((idx+1)/len(plan),f'{idx+1}/{len(plan)}  {pat["name"]}')
        store.save_json(out/'capture.json',{'timestamp':store.now(),'mode':mode,
            'pattern_config':cfg,'pattern_plan':plan,'exposure_us':camera.exposure,
            'demo':camera.demo,'iPad_browser':patterns.status()['browser'],'shots':shots})
        return {'images':len(shots),'path':str(out),'demo':camera.demo}
    finally:
        camera.acquiring=False
        if previous_preview and camera.status()['connected']:
            try:camera.start_preview()
            except Exception:pass

@app.post('/api/calibrations/{ident}/flat/capture')
def cal_capture(ident:str):
    mutable_cal(ident)
    base=store.entity_path('calibrations',ident)
    def run(progress):
        result=capture_sequence(base/'flat',progress,'flat')
        store.update('calibrations',ident,flat_captured=True)
        return result
    return new_job('标定平面镜采集',run)

@app.post('/api/calibrations/{ident}/flat/import')
async def cal_flat_zip(ident:str,file:UploadFile=File(...)):
    mutable_cal(ident)
    base=store.entity_path('calibrations',ident)
    saved=safe_image_zip(await file.read(),base/'flat')
    store.update('calibrations',ident,flat_captured=True)
    return {'saved':len(saved)}

@app.post('/api/calibrations/{ident}/solve')
def cal_solve(ident:str):
    rec=mutable_cal(ident)
    base=store.entity_path('calibrations',ident)
    if not (base/'intrinsics.npz').is_file():raise ValueError('请先导入内参')
    if not (base/'plane.json').is_file():raise ValueError('请先求解棋盘格参考平面')
    results=decode(base/'flat',base/'flat_decoded')
    summary=calibrate_screen(base/'flat_decoded'/'screen_uv.npz',
        store.read_json(base/'plane.json'),base/'intrinsics.npz',base/'geometry.npz',
        rec['screen_width_mm'],rec['screen_height_mm'])
    store.save_json(base/'geometry_result.json',summary)
    store.update('calibrations',ident,ready=True,status='ready',geometry_quality=summary,decode_quality=results)
    return {'geometry':summary,'decode':results}

@app.get('/api/measurements')
def measurements():return store.list_records('measurements')
@app.post('/api/measurements')
def create_run(payload:dict=Body(...)):
    cal_id=str(payload.get('calibration_id',''))
    cal=store.get('calibrations',cal_id)
    if not cal.get('ready'):raise ValueError('所选标定尚未完成')
    rec=store.create('measurements',payload.get('name',''),{
        'calibration_id':cal_id,'calibration_name':cal['name'],
        'pressure':str(payload.get('pressure',''))[:100],
        'notes':str(payload.get('notes',''))[:500],
        'roi':payload.get('roi'), 'source':'real', 'captured':False,
        'decoded':False,'reconstructed':False})
    store.clone_geometry(cal_id,rec['id'])
    return rec
@app.get('/api/measurements/{ident}')
def run_get(ident:str):return store.get('measurements',ident)
# Per-run image-space aperture. The mask is independent of the calibration.
def _roi_editable_run(ident: str):
    rec = store.get('measurements', ident)
    if not rec.get('captured'):
        raise ValueError('请先采集或导入相移图，再按白图圈定工作面')
    with JOB_LOCK:
        if any(job['status'] == 'running' for job in JOBS.values()):
            raise ValueError('正在采集/计算，完成后再修改掩膜')
    return rec, store.entity_path('measurements', ident)


def _archive_derived(base: Path, reason: str):
    """Never silently destroy previous results when a mask changes."""
    outputs = ['decoded', 'reconstruction']
    if not any((base / name).exists() for name in outputs):
        return None
    history = base / 'history' / store.uid('roi_revision')
    history.mkdir(parents=True, exist_ok=True)
    for name in outputs:
        source = base / name
        if source.exists():
            shutil.move(str(source), str(history / name))
    for name in ['roi_mask.png', 'roi.json']:
        if (base / name).is_file():
            shutil.copy2(base / name, history / ('previous_' + name))
    store.save_json(history / 'change.json', {'reason': reason, 'time': store.now()})
    return history.name


@app.get('/api/measurements/{ident}/roi')
def run_roi_get(ident: str):
    base = store.entity_path('measurements', ident)
    rec = store.get('measurements', ident)
    if not rec.get('captured'):
        return {'available': False, 'roi': None}
    w, h = roi_tools.raw_image_shape(base / 'raw')
    file = base / 'roi.json'
    return {'available': True, 'width': w, 'height': h,
            'roi': store.read_json(file) if file.is_file() else None}


@app.get('/api/measurements/{ident}/roi/preview')
def run_roi_white_preview(ident: str):
    import cv2
    import numpy as np
    from .processing import img_read
    base = store.entity_path('measurements', ident)
    im = img_read(base / 'raw' / 'white.png')
    h, w = im.shape[:2]
    if w > 1500:
        factor = 1500 / w
        im = cv2.resize(im, (1500, max(1, round(h * factor))),
                        interpolation=cv2.INTER_AREA)
    # A PNG might contain 16-bit grayscale. The overlay is for visual editing
    # only; raw 16-bit phase images are never altered by this conversion.
    if im.dtype != np.uint8:
        im = np.clip(im.astype(np.float32) / max(1, np.iinfo(im.dtype).max) * 255,
                     0, 255).astype('uint8')
    ok, result = cv2.imencode('.jpg', im, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        raise RuntimeError('无法生成白图预览')
    return Response(bytes(result), media_type='image/jpeg',
                    headers={'Cache-Control': 'no-store'})


@app.post('/api/measurements/{ident}/roi')
def run_roi_save(ident: str, payload: dict = Body(...)):
    rec, base = _roi_editable_run(ident)
    valid = roi_tools.validate_roi(payload)
    w, h = roi_tools.raw_image_shape(base / 'raw')
    # Verify before modifying any on-disk state.
    roi_tools.make_mask(valid, w, h)
    archived = _archive_derived(base, 'Manual ROI updated')
    meta = roi_tools.save_mask(base, valid)
    updated = store.update('measurements', ident, roi=meta,
        decoded=False, reconstructed=False, decode=None,
        reconstruction=None, status='captured')
    return {'roi': meta, 'archived_results': archived,
            'needs_decode': True, 'run_id': updated['id']}


@app.post('/api/measurements/{ident}/roi/reset')
def run_roi_reset(ident: str):
    rec, base = _roi_editable_run(ident)
    if not (base / 'roi.json').exists() and not (base / 'roi_mask.png').exists():
        return {'roi': None, 'needs_decode': bool(rec.get('decoded'))}
    archived = _archive_derived(base, 'Manual ROI cleared')
    (base / 'roi.json').unlink(missing_ok=True)
    (base / 'roi_mask.png').unlink(missing_ok=True)
    store.update('measurements', ident, roi=None, decoded=False,
                 reconstructed=False, decode=None, reconstruction=None,
                 status='captured')
    return {'roi': None, 'needs_decode': True,
            'archived_results': archived}


@app.post('/api/measurements/{ident}/capture')
def run_capture(ident:str):
    rec=store.get('measurements',ident)
    if rec.get('captured'):raise ValueError('数据单元已有原始图像，请创建新测量')
    base=store.entity_path('measurements',ident)
    def run(progress):
        result=capture_sequence(base/'raw',progress,'sample')
        store.update('measurements',ident,captured=True,status='captured',source='demo' if camera.demo else 'real')
        return result
    return new_job('自动采集：'+rec['name'],run)
@app.post('/api/measurements/{ident}/raw/import')
async def run_zip(ident:str,file:UploadFile=File(...)):
    rec=store.get('measurements',ident)
    if rec.get('captured'):raise ValueError('已有原始图像，禁止覆盖')
    base=store.entity_path('measurements',ident)
    saved=safe_image_zip(await file.read(),base/'raw')
    store.update('measurements',ident,captured=True,status='captured',source='imported')
    return {'saved':len(saved)}
@app.post('/api/measurements/{ident}/decode')
def run_decode(ident:str):
    rec=store.get('measurements',ident)
    if not rec.get('captured'):raise ValueError('请先采集或导入相移图')
    base=store.entity_path('measurements',ident)
    def run(progress):
        progress(.1,'解码中')
        roi_path = base / 'roi_mask.png'
        manual = roi_path if (base / 'roi.json').is_file() and roi_path.is_file() else None
        legacy_roi = rec.get('roi') if isinstance(rec.get('roi'), (list, tuple)) else None
        result=decode(base/'raw',base/'decoded',legacy_roi,
                      manual_mask_path=manual)
        progress(.95,'保存相位')
        store.update('measurements',ident,decoded=True,status='decoded',decode=result)
        return result
    return new_job('相位解码：'+rec['name'],run)
@app.post('/api/measurements/{ident}/reconstruct')
def run_reconstruct(ident:str,payload:dict=Body(...)):
    rec=store.get('measurements',ident)
    if not rec.get('decoded'):raise ValueError('请先完成相位解码')
    anchor=float(payload.get('anchor_mm',0))
    if not -50<=anchor<=50:raise ValueError('参考高度应在-50～50mm')
    base=store.entity_path('measurements',ident)
    def run(progress):
        result=reconstruct(base/'decoded'/'screen_uv.npz',
            base/'calibration_snapshot'/'geometry.npz',base/'reconstruction',
            anchor_mm=anchor,progress=progress)
        store.update('measurements',ident,reconstructed=True,status='reconstructed',
            reconstruction=result,curvature=None)
        return result
    return new_job('形貌重建：'+rec['name'],run)


@app.post('/api/measurements/{ident}/curvature')
def run_curvature(ident: str):
    rec = store.get('measurements', ident)
    if not rec.get('reconstructed'):
        raise ValueError('请先完成形貌重建')
    base = store.entity_path('measurements', ident)
    result = analyze_shape(base/'reconstruction'/'shape.npz',base/'reconstruction')
    store.update('measurements',ident,curvature=result)
    return result

@app.post('/api/measurements/{ident}/open-folder')
def run_open_folder(ident:str):
    import subprocess
    p=store.entity_path('measurements',ident)
    if os.name!='nt':
        raise ValueError('打开资源管理器只支持Windows；数据目录：'+str(p))
    subprocess.Popen(['explorer.exe',str(p)])
    return {'opened':str(p)}

@app.post('/api/calibrations/{ident}/open-folder')
def cal_open_folder(ident:str):
    import subprocess
    p=store.entity_path('calibrations',ident)
    if os.name!='nt':
        raise ValueError('打开资源管理器只支持Windows；数据目录：'+str(p))
    subprocess.Popen(['explorer.exe',str(p)])
    return {'opened':str(p)}

@app.get('/api/calibrations/{ident}/file/{filename:path}')
def cal_file(ident:str,filename:str):
    allowed={'board','flat','flat_decoded'}
    # Geometry is handled via .npz import rather than arbitrary browsing.
    if filename=='geometry.npz':
        path=store.data_file('calibrations',ident,filename)
    elif filename.startswith('flat_decoded/'):
        path=store.data_file('calibrations',ident,filename)
    else:raise ValueError('不允许访问该文件')
    if not path.is_file():raise FileNotFoundError(filename)
    return FileResponse(path)
@app.get('/api/measurements/{ident}/file/{filename:path}')
def run_file(ident:str,filename:str):
    if not (filename.startswith('reconstruction/') or filename.startswith('decoded/')
            or filename == 'roi_mask.png'):
        raise ValueError('只能查看处理结果或本次手动掩膜')
    path=store.data_file('measurements',ident,filename)
    if not path.is_file():raise FileNotFoundError(filename)
    return FileResponse(path,headers={'Cache-Control':'no-store'})

@app.on_event('shutdown')
def cleanup():
    try:camera.disconnect()
    except Exception:pass
