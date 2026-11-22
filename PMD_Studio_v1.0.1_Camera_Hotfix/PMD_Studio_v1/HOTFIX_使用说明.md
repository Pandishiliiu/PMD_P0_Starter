# PMD Studio v1.0.1 — gxipy 相机导入修复

**仅替换程序文件，不要删除或覆盖 `data`。** 修复包不包含实验数据。

1. 关闭原来正在运行的 PMD Studio 命令窗口（Ctrl+C）；保留 iPad 网页即可。
2. 解压本补丁，将其中 `PMD_Studio_v1` 目录的内容复制到已有的 `PMD_Studio_v1` 同名目录，选择**替换文件**。补丁只包含 `studio/camera.py`、`start.bat` 和检查工具。
3. 双击 `check_camera_sdk.bat`。如果出现 `GXIPY_IMPORT_OK`，说明Python SDK已找到。
4. 双击 `start.bat`，浏览器刷新 `http://127.0.0.1:8000/studio`，重新连接相机。启动后 iPad 页面也能重新连接。

## 如果检查仍提示 GXIPY_IMPORT_FAILED

相机 SDK 的 Python `gxipy` 包在本机不存在，或者不是旧 PMD 工程所使用的目录。打开 PowerShell：

```powershell
$root='D:\Optoelectronics\激光刀口法测曲率\Phase Measuring Deflectometry\PMD_P0_Starter'
Get-ChildItem -LiteralPath $root -Directory | Select-Object Name,FullName
& (Join-Path $root '.venv\Scripts\python.exe') -c "import gxipy; print(gxipy.__file__)"
```

- 如果最后一行能打印出 `gxipy` 路径，但 PMD Studio 仍失败，请把检查工具的完整输出发回来。
- 如果连旧工程也无法 `import gxipy`，请找到之前能运行 `capture_chessboard.py` 时所使用的 SDK 文件夹。设置 `PMD_GXIPY_DIR` 为**包含 `gxipy` 子文件夹的上一级路径**，再启动 PMD Studio。示例（路径请换成实际的）：

```powershell
$env:PMD_GXIPY_DIR='D:\...\PythonSDK'
Set-Location 'D:\Optoelectronics\激光刀口法测曲率\Phase Measuring Deflectometry\PMD_P0_Starter\PMD_Studio_v1'
.\start.bat
```

**不要尝试 `pip install galaxy`、`pip install gxipy` 等不确定来源的包。** 优先使用之前已经成功连接相机的厂商SDK；也不要改动已采集的测量文件。
