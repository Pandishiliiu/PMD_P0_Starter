HOTFIX for Daheng gxipy NameError: gx_get_image is not defined

Cause: gxipy conditionally defines gx_get_image only if the locally loaded
Galaxy DLL exports GXGetImageEx. Newer DLL versions may export only GXDQBufEx.

Fix: grab_gray() in acquire.py automatically uses get_image when available;
otherwise it uses dq_buf() and q_buf(), and copies pixel data before queueing
it again. This fixes both chessboard preview and 26-image PMD acquisition.

Unzip the two .py files into your current PMD_P0_Starter folder, overwriting
the existing files. Do not delete .venv. Close GalaxyView before testing.

This patch is statically checked and mock-tested; the user's camera must verify
runtime support. If it reports neither available, use a matching SDK gxipy.
