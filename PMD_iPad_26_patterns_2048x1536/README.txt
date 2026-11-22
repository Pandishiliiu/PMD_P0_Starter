PMD P0 iPad Static Phase-shift Patterns (Offline, no server / device link)

26 images, 2048 x 1536 pixels (landscape 4:3).
Same phase formula as PMD_P0_Starter/ipad.html:
  I = round(128 + 100*cos(2*pi*(cycles*j/N + phase/4)))
  cycles = 1,4,16; phase = 0,1,2,3; axis = x or y.
  black = 16, white = 245.

INSTRUCTIONS
1. Copy the whole 26-PNG sequence to your iPad. Display each image FULL SCREEN, in filename order 01..26, one at a time. Do NOT crop, zoom, stretch, rotate, use dynamic contrast, or hide a part of the pattern under toolbar overlays. Ensure screen stays in landscape and brightness fixed. For precise geometric PMD later, measure the ACTUALLY displayed pattern bounds on screen, particularly if the iPad aspect ratio is not 4:3.
2. Hold the mirror, camera, iPad and pressure fixed throughout 26 captures. Keep auto-exposure/auto-gain disabled on the camera. After every image switch, allow the display to stabilize and capture one camera frame.
3. Camera frames must be SAVED WITHOUT THE LEADING ORDINAL prefix, exactly as in manifest.json (black.png, white.png, x_f1_p0.png ... y_f16_p3.png) under one measurements folder.
4. Then run your current: python decode.py --input measurements\run01
5. IMPORTANT: acquire.py requires live iPad/server communication and CANNOT be used for this offline photo sequence. Use supplied capture_manual_pmd.py instead (optional).

Cropping/letterboxing in Photos apps is a geometry consideration; no longer assume the PNG pixels are equal to physical screen pixels.
