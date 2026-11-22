"""Local quadratic curvature analysis of an existing reconstructed PMD height map.

Supports elliptical apertures. Does not assume a sphere or derive the 3D
surface from its silhouette. Does not change captured images, ROI or shape.
"""
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.linalg import eigh


def analyze_shape(shape_file, output_dir, cell_mm=0.5):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from .store import save_json

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with np.load(shape_file, allow_pickle=False) as d:
        xx = np.asarray(d['x_mm'], dtype=float)
        yy = np.asarray(d['y_mm'], dtype=float)
        zz = np.asarray(d['h_mm'], dtype=float)
    ok = np.isfinite(xx) & np.isfinite(yy) & np.isfinite(zz)
    xx, yy, zz = xx[ok], yy[ok], zz[ok]
    if len(xx) < 250:
        raise ValueError('重建点不足，不能稳定计算双向曲率')

    # Equal-area ~0.5 mm bins mitigate unequal point density / edge weight.
    xy = np.column_stack([xx, yy])
    bins = np.floor((xy - xy.min(axis=0)) / cell_mm).astype(np.int32)
    _, inv = np.unique(bins, axis=0, return_inverse=True)
    order = np.argsort(inv)
    cut = np.r_[0, np.flatnonzero(np.diff(inv[order]))+1, len(order)]
    med = []
    for s, e in zip(cut[:-1], cut[1:]):
        idx = order[s:e]
        if len(idx) >= 3:
            med.append([np.median(xx[idx]), np.median(yy[idx]), np.median(zz[idx])])
    med = np.asarray(med, dtype=float)
    if len(med) < 60:
        raise ValueError('有效空间网格太少，无法稳定拟合')
    xc, yc, zc = med.mean(axis=0)
    X, Y, Z = (med - np.array([xc,yc,zc])).T
    B = np.column_stack([np.ones(len(X)),X,Y,X*X,X*Y,Y*Y])
    initial = np.linalg.lstsq(B,Z,rcond=None)[0]
    # Robust fit, but report ordinary unweighted RMS of residual, in micrometers.
    opt = least_squares(lambda p:B@p-Z,initial,loss='soft_l1',f_scale=0.01,
                        max_nfev=180)
    c0,p,q,a,b,c = opt.x
    residual_um = (Z-B@opt.x)*1000
    H = np.array([[2*a,b],[b,2*c]])
    g = np.array([p,q])
    # Metric and second fundamental form of z(X,Y).
    I = np.eye(2)+np.outer(g,g)
    II = H / np.sqrt(1+g@g)
    # Generalized symmetric eigenproblem: II v = k I v.
    k,eigvec=eigh(II,I)
    inds=np.argsort(-abs(k));k=k[inds];eigvec=eigvec[:,inds]

    def rho(kappa):
        if abs(kappa)<1e-9:return None
        return float(1/abs(kappa))
    def angle(v):
        return float(np.degrees(np.arctan2(v[1],v[0])) % 180)
    def normal_curvature(direction):
        v = np.asarray(direction,float)
        v /= np.linalg.norm(v)
        return float((v@II@v)/(v@I@v))

    # Aperture's observed major/minor directions; not necessarily material
    # principal directions or even a faithful aperture if coverage is partial.
    eigval,axis=eigh(np.cov(med[:,:2].T))
    major=axis[:,1];minor=axis[:,0]
    kmaj=normal_curvature(major)
    kmin=normal_curvature(minor)
    p1, p2 = rho(k[0]), rho(k[1])
    anisotropy = None
    if p1 and p2:
        anisotropy = float(200*abs(p1-p2)/(p1+p2))
    result = {
        'fit':'robust quadratic, local at spatial sample centroid',
        'number_of_bins':int(len(med)),
        'bin_size_mm':float(cell_mm),
        'center_xy_mm':[float(xc),float(yc)],
        'quadratic_coefficients':opt.x.tolist(),
        'quadratic_vertical_rms_um':float(np.sqrt(np.mean(residual_um**2))),
        'quadratic_vertical_pv_um':float(np.ptp(residual_um)),
        'principal':[
            {'radius_mm':rho(kv),'signed_curvature_per_mm':float(kv),
             'axis_angle_deg_from_x':angle(eigvec[:,i])}
            for i,kv in enumerate(k)
        ],
        'principal_radius_relative_difference_percent':anisotropy,
        'aperture':{
            'major_axis_angle_deg_from_x':angle(major),
            'minor_axis_angle_deg_from_x':angle(minor),
            'observed_axis_rms_ratio':float(np.sqrt(eigval[1]/eigval[0])),
            'radius_along_major_mm':rho(kmaj),
            'radius_along_minor_mm':rho(kmin),
            'signed_curvature_major_per_mm':kmaj,
            'signed_curvature_minor_per_mm':kmin,
        },
        'note':'P0: radii are local curvatures of the reconstructed height field; aperture ellipse alone does not imply an ellipsoidal surface. Results depend on optical calibration and assumed depth.'
    }
    save_json(output_dir/'directional_curvature.json',result)
    # Plot a concise residual map and show principal/aperture orientations.
    fig,ax=plt.subplots(figsize=(8.5,5.2))
    vlim=max(0.01,float(np.percentile(np.abs(residual_um),98)))
    pts=ax.scatter(med[:,0],med[:,1],c=residual_um,cmap='coolwarm',
                   vmin=-vlim,vmax=vlim,s=6,linewidths=0)
    span=np.ptp(med[:,:2],axis=0)
    length=0.23*float(np.min(span))
    for v,label,color in ((major,'Aperture long','#c28616'),
                          (eigvec[:,0],'Principal 1','#075d38')):
        v=v/np.linalg.norm(v)
        a0=np.array([xc,yc])-length*v;a1=np.array([xc,yc])+length*v
        ax.plot([a0[0],a1[0]],[a0[1],a1[1]],color=color,linewidth=2,label=label)
    ax.legend(loc='lower left',fontsize=8)
    ax.set_aspect('equal');ax.set_xlabel('X (mm)');ax.set_ylabel('Y (mm)')
    ax.set_title('Quadratic residual (P0)')
    fig.colorbar(pts,ax=ax,label='Height residual (µm)')
    fig.tight_layout();fig.savefig(output_dir/'quadratic_residual.png',dpi=150)
    plt.close(fig)
    return result
