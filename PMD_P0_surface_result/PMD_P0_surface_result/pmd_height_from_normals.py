"""Reference-plane PMD normal integration for P0 only.
Reads locally saved reflection_check.npz and mirror_plane.npz.
Performs 4-neighbor irregular-metric least-squares gradient integration.
Outputs reference-relative and plane-detrended heights; results are self-calibration residuals.
"""
from pathlib import Path
import json
import numpy as np
import cv2
from scipy import sparse
from scipy.sparse.linalg import lsqr
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ROOT = Path('measurements/run01_original26/geometry_p0')
PLANE = Path('measurements/mirror_plane_calibration/mirror_plane.npz')
OUT = ROOT / 'surface_reconstruction'
OUT.mkdir(parents=True, exist_ok=True)

D = np.load(ROOT / 'reflection_check.npz')
px = D['pixel_x'].astype(int)
py = D['pixel_y'].astype(int)
P = D['reference_points_mm'].astype(float)
N = D['estimated_normals'].astype(float)
REF = np.load(PLANE)
n0 = REF['n'].astype(float).ravel()
n0 /= np.linalg.norm(n0)
N *= np.where(N @ n0 >= 0, 1, -1)[:, None]

# Coordinates in local reference plane, in millimeters
e1 = np.array([1.0, 0, 0])
e1 -= np.dot(e1, n0) * n0
e1 /= np.linalg.norm(e1)
e2 = np.cross(n0, e1)
xy = np.column_stack((P @ e1, P @ e2))
c = N @ n0
slope_x = -(N @ e1) / c
slope_y = -(N @ e2) / c
theta = np.degrees(np.arccos(np.clip(np.abs(c), 0, 1)))

STEP = 8  # matching the prior reflection_check sampler
col = px // STEP
row = py // STEP
shape = (row.max() + 1, col.max() + 1)
valid = (np.isfinite(P).all(axis=1) & np.isfinite(N).all(axis=1)
         & np.isfinite(slope_x) & np.isfinite(slope_y)
         & (c > 0.001) & (theta <= 0.2))
raw_mask = np.zeros(shape, np.uint8)
raw_mask[row[valid], col[valid]] = 1
number, labels, stats, _ = cv2.connectedComponentsWithStats(raw_mask, 8)
if number < 2:
    raise RuntimeError('No valid connected reflective surface')
largest = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
selected = np.where(valid & (labels[row, col] == largest))[0]
if len(selected) < 100:
    raise RuntimeError('Too few reliable samples')

# Reindex selected samples as grid vertices
idmap = np.full(shape, -1, dtype=np.int32)
idmap[row[selected], col[selected]] = np.arange(len(selected), dtype=np.int32)
xy, P, N, slope_x, slope_y = (array[selected] for array in (xy, P, N, slope_x, slope_y))
px, py = px[selected], py[selected]
row, col = row[selected], col[selected]

# For every adjacent pair, enforce z_j-z_i = integral of measured slopes across the segment
edges = []
for a, b in ((idmap[:, :-1], idmap[:, 1:]),
             (idmap[:-1, :], idmap[1:, :])):
    ok = (a >= 0) & (b >= 0)
    edges.append((a[ok], b[ok]))
i = np.concatenate([a for a, _ in edges])
j = np.concatenate([b for _, b in edges])
M = len(i)
V = len(selected)
dxy = xy[j] - xy[i]
b = (0.5 * (slope_x[i] + slope_x[j]) * dxy[:, 0]
     + 0.5 * (slope_y[i] + slope_y[j]) * dxy[:, 1])
# Pin one height to zero (piston is unobservable)
rows = np.r_[np.arange(M), np.arange(M), M]
cols = np.r_[i, j, 0]
values = np.r_[-np.ones(M), np.ones(M), 1.0]
A = sparse.coo_matrix((values, (rows, cols)), shape=(M + 1, V)).tocsr()
solution = lsqr(A, np.r_[b, 0], atol=1e-7, btol=1e-7, iter_lim=2000)
h = solution[0]
h -= h.mean()

# Remove global best-fit tilt from reconstructed height (not from measured slopes)
fit_matrix = np.column_stack((xy, np.ones(V)))
plane_coeff, *_ = np.linalg.lstsq(fit_matrix, h, rcond=None)
h_flat = h - fit_matrix @ plane_coeff

# NumPy data and human-readable metrics
def stats(z):
    return {
        'rms_um': float(np.sqrt(np.mean(z**2)) * 1000),
        'pv_um': float(np.ptp(z) * 1000),
        'min_um': float(np.min(z) * 1000),
        'max_um': float(np.max(z) * 1000),
    }

report = {
    'method': 'Reference-plane slope integration, four-neighbor least squares',
    'disclaimer': 'Self-calibrated shape residual, NOT independent surface accuracy',
    'input_normals': int(len(D['pixel_x'])),
    'reliable_largest_component': int(V),
    'adjacent_edges': int(M),
    'normal_outlier_threshold_deg': 0.2,
    'lsqr_stop_code': int(solution[1]),
    'lsqr_iterations': int(solution[2]),
    'extent_x_mm': float(np.ptp(xy[:, 0])),
    'extent_y_mm': float(np.ptp(xy[:, 1])),
    'reference_relative': stats(h),
    'best_fit_plane_removed': stats(h_flat),
    'best_fit_tilt_deg': float(np.degrees(np.arctan(np.hypot(*plane_coeff[:2])))),
}
np.savez_compressed(
    OUT / 'height_reconstruction.npz',
    pixel_x=px, pixel_y=py, x_mm=xy[:, 0], y_mm=xy[:, 1],
    z_reference_um=h * 1000, z_detrended_um=h_flat * 1000,
    p=slope_x, q=slope_y, e1=e1, e2=e2, n_ref=n0,
)
(OUT / 'metrics.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
np.savetxt(OUT / 'surface_points.csv',
           np.column_stack([px, py, xy, h * 1000, h_flat * 1000]),
           fmt=['%d', '%d', '%.6f', '%.6f', '%.6f', '%.6f'],
           delimiter=',', comments='',
           header='pixel_x,pixel_y,x_mm,y_mm,z_reference_um,z_detrended_um')

# Flat physical-coordinate maps
for name, z, title in (
    ('height_reference_um.png', h * 1000, 'Height relative to reference plane'),
    ('height_detrended_um.png', h_flat * 1000, 'Height after removing fitted plane'),
):
    fig, ax = plt.subplots(figsize=(8, 6), dpi=180)
    vmax = np.percentile(np.abs(z), 99.5)
    sc = ax.scatter(xy[:, 0], xy[:, 1], c=z, s=0.7,
                    vmin=-vmax, vmax=vmax, cmap='coolwarm', rasterized=True)
    ax.set_aspect('equal')
    ax.set_xlabel('Mirror X (mm)')
    ax.set_ylabel('Mirror Y (mm)')
    ax.set_title(title + ' [um]')
    fig.colorbar(sc, ax=ax, label='Height (um)')
    fig.tight_layout()
    fig.savefig(OUT / name)
    plt.close(fig)

# 3-D sampled point cloud view (vertical scale exaggerated by nature of um units)
keep = np.arange(0, V, max(1, V // 6000))
fig = plt.figure(figsize=(9, 6), dpi=170)
ax = fig.add_subplot(111, projection='3d')
sc = ax.scatter(xy[keep, 0], xy[keep, 1], h_flat[keep] * 1000,
                c=h_flat[keep] * 1000, s=0.4, cmap='coolwarm', rasterized=True)
ax.set_xlabel('X (mm)')
ax.set_ylabel('Y (mm)')
ax.set_zlabel('Detrended height (um)')
ax.set_title('P0 relative surface reconstruction (self-calibration residual)')
fig.colorbar(sc, ax=ax, shrink=.6, label='Height (um)')
fig.tight_layout()
fig.savefig(OUT / 'height_3d_detrended.png')
plt.close(fig)

print('====== P0 SURFACE RECONSTRUCTION ======', flush=True)
print('Valid points:', V)
print('Physical extent: %.3f x %.3f mm' % (report['extent_x_mm'], report['extent_y_mm']))
print('LSQR stop code:', report['lsqr_stop_code'], 'iterations:', report['lsqr_iterations'])
print('Reference-plane-relative: RMS %.4f um, PV %.4f um' %
      (report['reference_relative']['rms_um'], report['reference_relative']['pv_um']))
print('Detrended height: RMS %.4f um, PV %.4f um' %
      (report['best_fit_plane_removed']['rms_um'], report['best_fit_plane_removed']['pv_um']))
print('Best-fit tilt: %.6f deg' % report['best_fit_tilt_deg'])
print('Saved to:', OUT.resolve())
print('WARNING: self-calibrated model residual only; not independent mirror accuracy.')
