"""PMD phase, planar geometry calibration, height integration, and shape fitting.
P0 research implementation: NOT a metrology-certified system.
"""
from __future__ import annotations
import json
import math
from pathlib import Path
import cv2
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import lsqr
from scipy.optimize import least_squares

# All image operations are reduced by factor 4 or 8 to keep RAM moderate.
DECIMATION = 4

def img_read(path, decimation=1):
    p=Path(path)
    if not p.is_file(): raise FileNotFoundError(p)
    arr=cv2.imdecode(np.fromfile(str(p),dtype=np.uint8),cv2.IMREAD_GRAYSCALE)
    if arr is None: raise ValueError(f'无法读取图像: {p}')
    return arr[::decimation,::decimation]

def img_save(path, image):
    p=Path(path); p.parent.mkdir(parents=True,exist_ok=True)
    ok,buf=cv2.imencode('.png',image)
    if not ok: raise ValueError('PNG encoding failed')
    buf.tofile(str(p))

def read_intrinsics(path):
    with np.load(path,allow_pickle=False) as g:
        K=np.asarray(g['K'],dtype=np.float64)
        D=np.asarray(g['dist'],dtype=np.float64).reshape(-1)
    if K.shape != (3,3) or len(D) not in (4,5,8,12,14):
        raise ValueError('K应为3×3, dist应包含4/5/8/12/14个系数')
    if not np.isfinite(K).all() or not np.isfinite(D).all() or not np.allclose(K[2],[0,0,1]):
        raise ValueError('相机内参包含无效值')
    return K,D

def board_plane(image_folder, intrinsics, inner=(9,6), square_mm=4.0):
    K,dist=read_intrinsics(intrinsics)
    nx,ny=map(int,inner)
    if not 2<=nx<=30 or not 2<=ny<=30 or not 0.01<float(square_mm)<1000:
        raise ValueError('棋盘格尺寸不合理')
    obj=np.zeros((nx*ny,3),np.float32)
    obj[:,:2]=np.mgrid[0:nx,0:ny].T.reshape(-1,2)*float(square_mm)
    results=[]
    for p in sorted(Path(image_folder).iterdir()):
        if p.suffix.lower() not in ('.png','.jpg','.jpeg','.bmp'):continue
        im=img_read(p)
        h,w=im.shape
        scale=min(1.,1200/max(h,w))
        small=cv2.resize(im,None,fx=scale,fy=scale,interpolation=cv2.INTER_AREA)
        found,corners=cv2.findChessboardCorners(small,(nx,ny),
            cv2.CALIB_CB_ADAPTIVE_THRESH|cv2.CALIB_CB_NORMALIZE_IMAGE)
        if not found:
            results.append({'file':p.name,'ok':False,'reason':'未识别角点'})
            continue
        corners=(corners/scale).astype(np.float32)
        corners=cv2.cornerSubPix(im,corners,(7,7),(-1,-1),
            (cv2.TERM_CRITERIA_EPS+cv2.TERM_CRITERIA_MAX_ITER,40,.001))
        ok,rvec,tvec=cv2.solvePnP(obj,corners,K,dist,
                flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok:
            results.append({'file':p.name,'ok':False,'reason':'PnP失败'})
            continue
        R,_=cv2.Rodrigues(rvec)
        n=R[:,2].astype(float)
        n/=np.linalg.norm(n)
        t=tvec.reshape(3).astype(float)
        d=-float(n@t)
        if d>0:n=-n;d=-d
        proj,_=cv2.projectPoints(obj,rvec,tvec,K,dist)
        rms=float(np.sqrt(np.mean(np.sum((proj.reshape(-1,2)-corners.reshape(-1,2))**2,axis=1))))
        results.append({'file':p.name,'ok':True,'n':n.tolist(),'d':d,'rms_px':rms})
    valid=[r for r in results if r['ok']]
    if not valid:raise ValueError('所有棋盘格均未能成功求解PnP；检查内角点9×6/方格4mm/对焦')
    normals=np.array([r['n'] for r in valid]);ds=np.array([r['d'] for r in valid])
    nmed=np.median(normals,axis=0); nmed/=np.linalg.norm(nmed)
    ang=np.degrees(np.arccos(np.clip(normals@nmed,-1,1)))
    keep=(np.abs(ds-np.median(ds))<=3.0)&(ang<=1.5)
    if not np.any(keep):
        keep=np.ones(len(valid),bool)
    # If only one board was captured, the uncertainty remains unknown.
    n=normals[keep].mean(axis=0);n/=np.linalg.norm(n)
    d=float(ds[keep].mean())
    for ix,r in enumerate(valid):r['accepted']=bool(keep[ix])
    summary={'n':n.tolist(),'d':d,'num_used':int(keep.sum()),
             'num_valid':len(valid),'distance_std_mm':float(np.std(ds[keep])),
             'normal_max_dev_deg':float(np.max(ang[keep])),
             'median_pnp_rms_px':float(np.median([r['rms_px'] for r in valid if r['accepted']])),
             'boards':results}
    return summary

def phase_four(folder,prefix,skip):
    ims=[img_read(Path(folder)/f'{prefix}_p{k}.png',skip).astype(np.float32) for k in range(4)]
    a=ims[0]-ims[2];b=ims[3]-ims[1]
    modulation=np.hypot(a,b)*.5
    wrapped=np.mod(np.arctan2(b,a)/(2*np.pi),1.)
    return wrapped,modulation

def decode(folder,out,roi=None,skip=DECIMATION,manual_mask_path=None):
    folder=Path(folder);out=Path(out);out.mkdir(parents=True,exist_ok=True)
    black=img_read(folder/'black.png',skip).astype(np.float32)
    white=img_read(folder/'white.png',skip).astype(np.float32)
    if black.shape!=white.shape:raise ValueError('图片尺寸不一致')
    illum=white-black
    geo=(illum>20)
    if roi is not None:
        x1,y1,x2,y2=map(int,roi)
        yy,xx=np.ogrid[:geo.shape[0],:geo.shape[1]]
        geo &= (xx>=x1/skip)&(xx<x2/skip)&(yy>=y1/skip)&(yy<y2/skip)
    # The user-defined region restricts the physical *measurement aperture*.
    # Intersect BEFORE phase validity and connected-component filtering.
    # Never expand/replace low modulation pixels with the manually chosen area.
    manual_coverage = None
    if manual_mask_path is not None:
        user_mask = img_read(manual_mask_path, skip)
        if user_mask.shape != geo.shape:
            raise ValueError('手动掩膜与原始图像尺寸不一致，禁止错误缩放')
        user_mask = (user_mask > 127)
        manual_coverage = float(user_mask.mean())
        geo &= user_mask
    def axis(axis):
        w1,m1=phase_four(folder,f'{axis}_f1',skip)
        w4,m4=phase_four(folder,f'{axis}_f4',skip)
        k=np.rint(4*w1-w4)
        coord=(w4+k)/4.
        valid=(m1>8)&(m4>8)&(np.abs(coord-w1)<.04)&(coord>=0)&(coord<=1)
        return coord,valid,m1,m4
    u,vx,mx1,mx4=axis('x')
    v,vy,my1,my4=axis('y')
    mask=geo&vx&vy
    labels_count,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    if labels_count>1:
        largest=1+np.argmax(stats[1:,cv2.CC_STAT_AREA])
        mask=labels==largest
    if int(mask.sum())<100:
        raise RuntimeError('解码有效像素过少；先检查相移顺序、过曝、相机缓存和iPad尺寸')
    u[~mask]=np.nan;v[~mask]=np.nan
    np.savez_compressed(out/'screen_uv.npz',u=u.astype(np.float32),v=v.astype(np.float32),
                        mask=mask.astype(np.uint8),decimation=skip)
    img_save(out/'mask.png',(mask*255).astype(np.uint8))
    for label,coord in [('u',u),('v',v)]:
        img_save(out/f'{label}.png',np.nan_to_num(coord,nan=0).clip(0,1).__mul__(255).astype(np.uint8))
    meta={'valid_pixels':int(mask.sum()),'valid_fraction':float(mask.mean()),
          'illuminated_pixels':int(geo.sum()),
          'coverage_of_bright_area':float(mask.sum()/max(1,geo.sum())),
          'median_mod_x4':float(np.median(mx4[mask])),
          'median_mod_y4':float(np.median(my4[mask])),
          'decimation':skip,'image_size':list(black.shape[::-1]),
          'manual_roi_applied': manual_mask_path is not None,
          'manual_roi_fraction':manual_coverage,
          'u_range':list(map(float,[np.nanmin(u),np.nanmax(u)])),
          'v_range':list(map(float,[np.nanmin(v),np.nanmax(v)]))}
    from .store import save_json
    save_json(out/'decode.json',meta)
    return meta

def calibrate_screen(flat_decoded,calib_plane,intrinsics,out,screen_width_mm,screen_height_mm):
    K,dist=read_intrinsics(intrinsics)
    plane=calib_plane
    n=np.asarray(plane['n'],float);n/=np.linalg.norm(n)
    d=float(plane['d'])
    with np.load(flat_decoded,allow_pickle=False) as data:
        u=data['u'];v=data['v'];mask=data['mask'].astype(bool)
        skip=int(data['decimation'])
    yi,xi=np.where(mask&np.isfinite(u)&np.isfinite(v))
    if len(xi)<200:raise RuntimeError('平面镜有效区域不足')
    rng=np.random.default_rng(2026)
    if len(xi)>6500:
        sample=rng.choice(len(xi),6500,replace=False);yi=yi[sample];xi=xi[sample]
    W,H=float(screen_width_mm),float(screen_height_mm)
    pimg=np.column_stack([xi*skip,yi*skip]).astype(np.float64)
    uv=np.column_stack([u[yi,xi]*W,v[yi,xi]*H]).astype(np.float64)
    physical=np.column_stack([uv,np.zeros(len(uv))])
    und=cv2.undistortPoints(pimg.reshape(-1,1,2),K,dist,P=K).reshape(-1,2)
    homography,inliers=cv2.findHomography(uv,und,cv2.RANSAC,5.,maxIters=8000,confidence=.999)
    if homography is None:raise RuntimeError('无法估计虚拟屏幕单应性')
    keep=inliers.reshape(-1).astype(bool)
    if int(keep.sum())<100:raise RuntimeError('虚拟屏幕标定内点不足')
    ok,rvec,tvec=cv2.solvePnP(physical[keep],pimg[keep],K,dist,flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok:raise RuntimeError('虚拟屏幕PnP失败')
    Rv,_=cv2.Rodrigues(rvec);tv=tvec.ravel()
    project,_=cv2.projectPoints(physical,rvec,tvec,K,dist)
    errors=np.linalg.norm(project.reshape(-1,2)-pimg,axis=1)
    reflect=lambda P:P-2*(np.dot(n,P)+d)*n
    reflect_vec=lambda V:V-2*np.dot(n,V)*n
    S0=reflect(tv);ex=reflect_vec(Rv[:,0]);ey=reflect_vec(Rv[:,1]);
    ex/=np.linalg.norm(ex);ey/=np.linalg.norm(ey)
    ez=np.cross(ex,ey)
    same_side=bool(d*(np.dot(n,S0)+d)>0)
    if not same_side:raise RuntimeError('屏幕与相机位于平面镜两侧，可能是棋盘格/单应性标定错误')
    Path(out).parent.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(out,K=K,dist=dist,mirror_n=n,mirror_d=d,
        screen_origin=S0,screen_ex=ex,screen_ey=ey,screen_ez=ez,
        screen_width_mm=W,screen_height_mm=H,virtual_R=Rv,virtual_t=tv,
        ransac_fraction=float(keep.mean()),reprojection_median_px=float(np.median(errors)),
        reprojection_p90_px=float(np.percentile(errors,90)))
    return {'inlier_fraction':float(keep.mean()),'inliers':int(keep.sum()),
            'sampled':len(keep),'median_reprojection_px':float(np.median(errors)),
            'p90_reprojection_px':float(np.percentile(errors,90)),
            'screen_origin_mm':S0.tolist(),'camera_screen_same_side':same_side,
            'warning':'系统几何误差较大；请检查平面镜与棋盘格共面' if np.median(errors)>5 else ''}

def reconstruct(decoded_file,geometry_file,out,anchor_mm=0.,step=2,max_iter=12,progress=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .store import save_json
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    with np.load(geometry_file,allow_pickle=False) as g:
        K=g['K'].astype(float);dist=g['dist'].astype(float)
        n=g['mirror_n'].astype(float).ravel();d=float(g['mirror_d'])
        S0=g['screen_origin'].astype(float).ravel();ex=g['screen_ex'].astype(float).ravel()
        ey=g['screen_ey'].astype(float).ravel();W=float(g['screen_width_mm']);H=float(g['screen_height_mm'])
    n/=np.linalg.norm(n)
    with np.load(decoded_file,allow_pickle=False) as p:
        u=p['u'][::step,::step];v=p['v'][::step,::step]
        mask=p['mask'][::step,::step].astype(bool)
        pixel_step=int(p['decimation'])*step
    e1=np.array([1.,0.,0.]);e1-=np.dot(e1,n)*n;e1/=np.linalg.norm(e1)
    e2=np.cross(n,e1)
    good=mask&np.isfinite(u)&np.isfinite(v)&(u>=0)&(u<=1)&(v>=0)&(v<=1)
    yi,xi=np.where(good)
    if len(xi)<500:raise RuntimeError('重建有效采样过少')
    U=u[yi,xi];V=v[yi,xi]
    S=S0[None,:]+(U*W)[:,None]*ex+(V*H)[:,None]*ey
    pixels=np.column_stack([xi*pixel_step,yi*pixel_step]).astype(float)
    und=cv2.undistortPoints(pixels.reshape(-1,1,2),K,dist).reshape(-1,2)
    rays=np.column_stack([und,np.ones(len(und))]);rays/=np.linalg.norm(rays,axis=1,keepdims=True)
    rn=rays@n
    valid=np.isfinite(rn)&(rn>.1)&np.isfinite(S).all(axis=1)
    xi=xi[valid];yi=yi[valid];U=U[valid];V=V[valid];S=S[valid];rays=rays[valid];rn=rn[valid]
    N=len(xi);ids=np.full(mask.shape,-1,np.int32);ids[yi,xi]=np.arange(N)
    edges=[]
    for a,b in ((ids[:,:-1],ids[:,1:]),(ids[:-1,:],ids[1:,:])):
        hit=(a>=0)&(b>=0);edges.append((a[hit],b[hit]))
    i=np.r_[edges[0][0],edges[1][0]];j=np.r_[edges[0][1],edges[1][1]]
    ok=(np.abs(U[j]-U[i])<.12)&(np.abs(V[j]-V[i])<.12)
    i=i[ok];j=j[ok]
    if len(i)<500:raise RuntimeError('可积分的相邻像素不足')
    graph=sparse.coo_matrix((np.ones(2*len(i)),(np.r_[i,j],np.r_[j,i])),shape=(N,N)).tocsr()
    _,labels=connected_components(graph,directed=False)
    biggest=np.argmax(np.bincount(labels));keep=labels==biggest
    remap=np.full(N,-1,np.int32);remap[keep]=np.arange(keep.sum())
    edgekeep=keep[i]&keep[j];i=remap[i[edgekeep]];j=remap[j[edgekeep]]
    xi=xi[keep];yi=yi[keep];S=S[keep];rays=rays[keep];rn=rn[keep]
    N=len(xi);E=len(i)
    if N<500:raise RuntimeError('最大连通区域不足500点')
    anchor=int(np.argmin((xi-np.median(xi))**2+(yi-np.median(yi))**2))
    A=sparse.coo_matrix((np.r_[-np.ones(E),np.ones(E),20.],
        (np.r_[np.arange(E),np.arange(E),E],np.r_[i,j,anchor])),shape=(E+1,N)).tocsr()
    h=np.full(N,float(anchor_mm))
    def geo(h):
        t=(h-d)/rn
        if np.any(t<0):raise RuntimeError('镜面在相机背面；参考深度无效')
        P=t[:,None]*rays
        xy=np.column_stack([P@e1,P@e2])
        vc=-P;vc/=np.linalg.norm(vc,axis=1,keepdims=True)
        vs=S-P;vs/=np.linalg.norm(vs,axis=1,keepdims=True)
        nm=vc+vs;nm_norm=np.linalg.norm(nm,axis=1)
        if np.any(nm_norm<1e-8):raise RuntimeError('反射几何退化')
        nm/=nm_norm[:,None];nm*=np.where(nm@n>=0,1.,-1.)[:,None]
        den=nm@n
        if np.any(den<.2):raise RuntimeError('镜面倾角超出当前参考模型适用范围')
        sx=-(nm@e1)/den;sy=-(nm@e2)/den
        return xy,sx,sy
    iteration_log=[]
    for it in range(int(max_iter)):
        xy,sx,sy=geo(h)
        dr=xy[j]-xy[i]
        delta=.5*(sx[i]+sx[j])*dr[:,0]+.5*(sy[i]+sy[j])*dr[:,1]
        sol=lsqr(A,np.r_[delta,20*anchor_mm],atol=1e-7,btol=1e-7,iter_lim=1600)
        h2=sol[0]
        if not np.isfinite(h2).all() or np.max(np.abs(h2))>60:
            raise RuntimeError('重建高度发散，检查几何和深度基准')
        change=float(np.sqrt(np.mean((h2-h)**2)))
        h=.4*h+.6*h2
        iteration_log.append({'iteration':it+1,'height_PV_mm':float(np.ptp(h)),
            'change_RMS_mm':change,'lsqr_stop':int(sol[1]),'lsqr_iterations':int(sol[2])})
        if progress:progress((it+1)/max_iter, f'重建迭代 {it+1}/{max_iter}')
        if change<.0005:break
    xy,sx,sy=geo(h)
    dr=xy[j]-xy[i]
    delta=.5*(sx[i]+sx[j])*dr[:,0]+.5*(sy[i]+sy[j])*dr[:,1]
    grad_res=(h[j]-h[i])-delta
    # P0 best-fit plane of the HEIGHT FIELD, not a metrology reference plane.
    M=np.column_stack([xy,np.ones(N)])
    plane_coeff=np.linalg.lstsq(M,h,rcond=None)[0]
    hflat=h-M@plane_coeff;hflat-=hflat.mean()
    np.savez_compressed(out/'shape.npz',x_mm=xy[:,0].astype(np.float32),
        y_mm=xy[:,1].astype(np.float32),h_mm=h.astype(np.float32),
        h_detrended_um=(hflat*1000).astype(np.float32),
        pixel_x=(xi*pixel_step),pixel_y=(yi*pixel_step))
    np.savetxt(out/'points.csv',np.column_stack([xy,h*1000,hflat*1000]),delimiter=',',
               header='x_mm,y_mm,height_um,detrended_um',comments='')
    def plot_height(name,values,title):
        fig,ax=plt.subplots(figsize=(8,5))
        im=ax.scatter(xy[:,0],xy[:,1],c=values,s=1.5,marker='s',linewidths=0,cmap='coolwarm')
        ax.set_aspect('equal');ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)');ax.set_title(title)
        fig.colorbar(im,ax=ax,label='Height (µm)');fig.tight_layout();fig.savefig(out/name,dpi=150);plt.close(fig)
    plot_height('height_raw.png',h*1000,'Raw height (P0)')
    plot_height('height_detrended.png',hflat*1000,'Best-fit plane removed (P0)')
    fig=plt.figure(figsize=(8,5));ax=fig.add_subplot(111,projection='3d')
    subset=np.arange(0,N,max(1,N//18000))
    ax.scatter(xy[subset,0],xy[subset,1],hflat[subset]*1000,c=hflat[subset]*1000,cmap='coolwarm',s=1)
    ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)');ax.set_zlabel('Height (µm)')
    ax.set_title('P0 surface – Z exaggeration may apply');fig.tight_layout()
    fig.savefig(out/'height_3d.png',dpi=130);plt.close(fig)
    result={'points':int(N),'extent_x_mm':float(np.ptp(xy[:,0])),
            'extent_y_mm':float(np.ptp(xy[:,1])),'raw_pv_um':float(np.ptp(h)*1000),
            'detrended_pv_um':float(np.ptp(hflat)*1000),
            'detrended_rms_um':float(np.sqrt(np.mean(hflat*hflat))*1000),
            'gradient_edge_rms_um':float(np.sqrt(np.mean(grad_res*grad_res))*1000),
            'plane_coeff':plane_coeff.tolist(),'anchor_mm':float(anchor_mm),'iterations':iteration_log,
            'sphere':None,'note':'P0 reconstruction: shape and radius depend on geometry and assumed depth.'}
    # Optional ball model: descriptive, never constrains reconstruction.
    cell=.5
    ci=np.floor((xy-xy.min(axis=0))/cell).astype(np.int32)
    _,inv=np.unique(ci,axis=0,return_inverse=True)
    order=np.argsort(inv);bounds=np.r_[0,np.flatnonzero(np.diff(inv[order]))+1,N]
    med=[]
    for start,end in zip(bounds[:-1],bounds[1:]):
        ids2=order[start:end]
        if len(ids2)>=3:med.append([np.median(xy[ids2,0]),np.median(xy[ids2,1]),np.median(h[ids2])])
    med=np.asarray(med)
    if len(med)>100:
        origin=med.mean(axis=0);Pc=med-origin
        xx,yy,zz=Pc.T
        B=np.column_stack([np.ones(len(xx)),xx,yy,xx**2,xx*yy,yy**2])
        coeff=np.linalg.lstsq(B,zz,rcond=None)[0]
        c0,px,py,a,b,c=coeff;Hess=np.array([[2*a,b],[b,2*c]])
        g=np.array([px,py]);I=np.eye(2)+np.outer(g,g);II=Hess/math.sqrt(1+g@g)
        k=np.linalg.eigvals(np.linalg.solve(I,II)).real
        r1=float(1/abs(k[0])) if abs(k[0])>1e-9 else None
        r2=float(1/abs(k[1])) if abs(k[1])>1e-9 else None
        km=float(np.mean(k))
        result['quadratic']={'R1_mm':r1,'R2_mm':r2,'rms_um':float(np.sqrt(np.mean((zz-B@coeff)**2))*1000)}
        if abs(km)>1e-7:
            normal=np.array([-px,-py,1.]);normal/=np.linalg.norm(normal)
            C0=np.array([0.,0.,c0])+normal/km
            p0=np.r_[C0,abs(1/km)]
            def resid(params):return np.linalg.norm(Pc-params[:3],axis=1)-params[3]
            sol=least_squares(resid,p0,loss='soft_l1',f_scale=.025,max_nfev=300,x_scale='jac')
            error=resid(sol.x)
            if sol.success and sol.x[3]>0:
                result['sphere']={'radius_mm':float(sol.x[3]),
                  'rms_um':float(np.sqrt(np.mean(error*error))*1000),
                  'pv_um':float(np.ptp(error)*1000)}
                fig,ax=plt.subplots(figsize=(8,5))
                im=ax.scatter(med[:,0],med[:,1],c=error*1000,cmap='coolwarm',s=9)
                ax.set_aspect('equal');ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)')
                ax.set_title(f'Sphere fit R={sol.x[3]:.2f} mm (P0)')
                fig.colorbar(im,ax=ax,label='Radial residual (µm)')
                fig.tight_layout();fig.savefig(out/'sphere_residual.png',dpi=150);plt.close(fig)
    save_json(out/'result.json',result)
    return result
