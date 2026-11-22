# 单目 PMD P0：大恒工业相机 + iPad 启动包

## 主工程（本包不替代它）

**Sandia OpenCSP / SOFAST Fringe**：https://github.com/sandialabs/OpenCSP

它包含成熟的镜面条纹处理、光学斜率求解、系统几何标定、拟合/重建和可视化。
SOFAST 完整处理流程需要生成本机的 `camera`、`screen_shape`、`spatial_orientation` 等标定文件，**不能直接把 iPad + 大恒相机原始图像送进去就声称得到真实形貌**。
本包是与原工程配套的 P0 **采集与绝对相位测试前端**：26 张 iPad 图案自动显示并通过大恒 Python SDK 采图，然后解出相机像素对应的屏幕归一化坐标 `u,v`；另有独立的相机内参标定。这不是毫米量纲高度测量。

## Windows 快速步骤

```powershell
# 项目已解压到指定目录；PowerShell 中进入该目录：
Set-Location -LiteralPath 'D:\Optoelectronics\激光刀口法测曲率\Phase Measuring Deflectometry\PMD_P0_Starter'
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
# 确保已经安装大恒 Galaxy SDK（包含 GxIAPI）；优先用随 SDK 的官方 gxipy
python -c "import gxipy as gx; print(gx.__file__)"
# 如果出现 ModuleNotFoundError，且未找到 SDK 自带的 gxipy：
# pip install iai-gxipy
python probe_camera.py
python synthetic_selftest.py
```

如果 PowerShell 不允许执行 Activate.ps1，可用命令提示符运行 `.venv\Scripts\activate.bat`。
关闭 **GalaxyView** 后再运行 `probe_camera.py`/`acquire.py`，避免独占设备冲突。

## iPad 图案（无需把 26 张 PNG 手动发送到 iPad）

保持相机/iPad/镜面三者位置固定，iPad 设置最大自动锁屏时间、关闭原彩 True Tone / 夜览 Night Shift / 自动亮度，亮度固定在中等水平，横屏。电脑与 iPad 在同一个可互访的 Wi-Fi 局域网。

```powershell
python serve_ipad.py
```

电脑上 `ipconfig` 找 **电脑无线局域网适配器 IPv4**，iPad Safari 输入 `http://电脑IPv4:8000`。不要输入 `localhost`。可通过分享 → 添加到主屏幕再从主屏幕打开，减少 Safari 地址栏占用；调整好后不可旋转屏幕。允许 Windows 防火墙的局域网访问。

**P0 注意**：网页绘制的花纹是覆盖浏览器**可视区**，不是假定的 iPad 标称分辨率。后续要用 `metadata.json` 中屏幕 raster 宽高 + 实测 iPad 有效发光区物理宽高来恢复屏幕毫米坐标。最终几何标定必须另外完成。

## 自动采集 26 张原始图

另开 PowerShell 窗口，激活同一虚拟环境；退出 GalaxyView。先用 GalaxyView 确认相机能看到镜面中 iPad 的清晰倒影、**不是相机直接看到 iPad**；随后关闭 GalaxyView 再运行：

```powershell
python acquire.py --output measurements\flat_run01 --exposure-us 10000
python decode.py --input measurements\flat_run01
```

全序列是：黑/白各 1 张 + X/Y 两方向 × (1,4,16) 整屏周期数 × 4 步相移，共 26 张。脚本将 iPad 图案切换后待收到 iPad 绘制 ACK，再等默认 0.75 秒并刷新若干相机帧，降低不同步风险。若画面饱和，调低曝光时间（如 `--exposure-us 3000`），不要开启自动曝光和自动增益。

`decoded/` 输出 `screen_uv.npz`、`screen_u.png`、`screen_v.png`、`mask.png`、`modulation.png`、`diagnostics.json`。图像是屏幕点坐标编码结果，不是测量的高度图。改变气压不应改变系统中相机或 iPad 的位置；后续取三组重复采集评估稳定性。

**相机色彩注记**：程序原样保存 2D Mono 原始数组；如果你的相机是 Bayer 彩色型号，2D Bayer RAW 不是已解码灰度图，需要先转换为灰度（或启用对应 SDK 转换），不可直接把 Bayer 棋盘纹当成亮度。运行前先确认型号/PixelFormat。

## 相机内参标定（必须单独做）

```powershell
python make_board.py
```

按 **实际尺寸 100%** 打印生成的 A4 `chessboard_A4_9x6_inner_8mm_300dpi.png`，用卡尺量单格边长应为 **8.00 mm**，不能选“适合页面”。棋盘**内角点** 9×6。让相机**直接**拍棋盘（不要通过气动镜反射），镜头焦距、对焦、分辨率、光圈在标定与采集期间保持不变；改变棋盘距离/位置/倾角，拍 15–25 张，尽量覆盖像面四角。可以运行 `python capture_chessboard.py`，按 **空格** 保存每张原始图，按 **q** 结束；图像保存在 `calibration\camera_images\`。

```powershell
python calibrate_camera.py --images calibration\camera_images --square-mm 8 --output calibration\camera_intrinsics.npz
```

输出 `K`、畸变系数、逐张重投影误差与 RMS。0.3–0.5 pixel 是 P0 诊断参考而非精度保证；高于这个范围优先排查照片模糊、棋盘不平、覆盖姿态少、角点误识别。

## 几何标定与真正 3D 形貌 —— **下一阶段，不可跳过**

单目 PMD 完整几何还需要：
1. 相机内参和畸变 `K, dist`（本包提供）。
2. 固定镜面**参考面/坐标系**及其相对于相机的姿态；推荐平面前表面镜 + 参考坐标标记。
3. iPad 显示面**真实空间位置/姿态/有效物理尺寸**，不是只知道屏幕宽高、支架距离。可借鉴 OpenCSP 的 `sofast_calibration`：空间取向校准和屏幕形状/点位校准。
4. 从 `u,v` 解出的**实际屏幕点 Q**，相机光线 C→P，重建表面法向及斜率；选择适用的参考高度/曲面先验，积分/拟合重建，再对比标准平面和气压变化。

单目经典 PMD 不能仅凭一组反射相位和相机内参唯一恢复绝对高度。用任意“相位×常数＝高度”的代码都会伪造量纲或错误精度。SOFAST 对未知曲面需要合理的测量点约束/初始曲面估计，不是无条件的全自由绝对 3D 恢复。

SOFAST 文档：
- https://opencsp.sandia.gov/sofast-2-0/
- https://opencsp.readthedocs.io/en/latest/example/sofast_calibration/index.html
- https://opencsp.readthedocs.io/en/latest/example/sofast_fringe/index.html

## P0 通关标准

1. `probe_camera.py` 找到一台大恒相机，能控固定曝光。
2. iPad 被 Windows 服务器控制显示全部 26 帧，文件名称与图案一一对应。
3. `screen_u.png`、`screen_v.png` 连续单调；`mask.png` 覆盖镜面主要有效区；没有大面积饱和/掉帧。
4. 运行相机标定，获取 K、畸变与可接受重投影残差。
5. 再进行全几何校准（必须外加平面参考镜或可替代的可靠几何约束），最后用 OpenCSP 重建斜率与定量曲面。

> 路径说明：本工程已将 OpenCV 文件读写改为兼容 Windows 中文路径的方式。保持整个项目在 `D:\Optoelectronics\激光刀口法测曲率\Phase Measuring Deflectometry\PMD_P0_Starter` 中运行即可。
