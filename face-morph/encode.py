import subprocess, os

for crf in range(16, 45, 2):
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", "24",
        "-i", "frames/%03d.png", "-c:v", "libx264", "-crf", str(crf),
        "-preset", "veryslow", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", "-an", "morph.mp4"], check=True)
    size = os.path.getsize("morph.mp4")
    print(f"crf {crf}: {size} bytes")
    if size < 95000:
        print("Done! Using crf", crf)
        break
else:
    print("Too big even at crf 44. Lower S in face_morph.py and redo.")