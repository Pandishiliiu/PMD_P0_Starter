# PMD Studio v1.0.2 — 相机实时预览帧率修复

## 问题来源

v1.0.1 的相机预览存在**两层人为限速**：

- 前端 app.js 每 **450 ms** 才拉取一张 JPEG，理论上最高只有约 **2.2 fps**。
- Python 后端每次预览拍摄结束后**额外暂停 180 ms**；全分辨率图像缩小到 960 像素时使用 `INTER_AREA`，增加 CPU 开销。

这与 GalaxyView 原生窗口流畅程度有明显差距，不能据此判断相机硬件本身帧率偏低。

## 本补丁改动

- `studio/camera.py`：连续相机触发、快速降采样、JPEG 缓存，保留单一相机所有权；相机帧只保留最新一帧，丢弃过时的预览帧；预览FPS显示。
- `studio/app.py`：新增 `/api/camera/stream` 的 MJPEG 实时流接口，保留旧 `/api/camera/frame` 兼容接口。
- `web/app.js`：改用一个持续的实时流链接，不再每450 ms重新请求一张图片。打开相机标签页才观看，切换标签或停止预览时关闭链接。

**不修改**相位解码、几何标定、形貌重建、26帧原图保存代码和任何测量数据。

## 安装

1. 退出 PMD Studio 服务，关闭其 Powershell 或在服务窗口按 `Ctrl+C`。
2. 将补丁解压到**包含 `PMD_Studio_v1` 文件夹的父目录**，选择覆盖同名文件。例如原安装路径为：

   `D:\Optoelectronics\激光刀口法测曲率\Phase Measuring Deflectometry\PMD_P0_Starter\PMD_Studio_v1.0_Windows\PMD_Studio_v1`

   则补丁应该解压到 `...\PMD_P0_Starter\PMD_Studio_v1.0_Windows`。
3. 继续用原来的 `PMD_Studio_v1\start.bat` 启动服务。
4. 电脑浏览器按 **Ctrl+F5** 强制刷新 `http://127.0.0.1:8000/studio`（若有多个浏览器页，关闭多余标签页）。
5. 在「相机」页点击「开始预览」。下方将显示**预览实际帧率**。

## 可选调优

在启动前设置环境变量（Windows PowerShell，需在同一个终端启动）：

```powershell
$env:PMD_PREVIEW_FPS = '24'       # 默认最多24 fps
$env:PMD_PREVIEW_WIDTH = '960'   # 默认预览宽度960像素
& '.\start.bat'
```

例如CPU占用较高时，将 `PMD_PREVIEW_WIDTH` 改成 `720`，`PMD_PREVIEW_FPS` 改成 `15`。
这是**预览参数**，不会改变软件触发采集的正式原始图分辨率（4024×3036）。

## 硬件边界

由于每一帧仍经过工业相机USB传输、软件触发、灰度缩略图处理、JPEG编码及浏览器显示，实际帧率由曝光、相机读出速度、CPU和USB带宽决定。该补丁解除原程序的2.2 fps前端限制，但不保证所有电脑达到24 fps。

测试：已通过离线模拟相机+真实HTTP MJPEG流接口通信测试；没有在用户的Windows大恒相机上进行现场联调。
