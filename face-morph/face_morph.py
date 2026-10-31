
import cv2
import numpy as np
import mediapipe as mp
import os
import subprocess
import glob
from scipy.spatial import Delaunay

# -----------------------------------
# 1. LOAD IMAGES
# -----------------------------------

img1 = cv2.imread("arthur.jpg")
img2 = cv2.imread("rachel.jpg")

if img1 is None or img2 is None:
    raise FileNotFoundError("Check that both photographs exist.")

SIZE = 256

# -----------------------------------
# 2. DETECT FACIAL LANDMARKS
# -----------------------------------

mp_face = mp.solutions.face_mesh

def detect_landmarks(image):

    with mp_face.FaceMesh(
        static_image_mode=True,
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5
    ) as detector:

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        result = detector.process(rgb)

    if not result.multi_face_landmarks:
        raise ValueError("No face detected!")

    h, w = image.shape[:2]

    points = np.array([
        [p.x * w, p.y * h]
        for p in result.multi_face_landmarks[0].landmark
    ], dtype=np.float32)

    return points


# -----------------------------------
# 3. CROP AND ALIGN FACES
# -----------------------------------

def prepare_face(image, points):

    xmin, ymin = points.min(axis=0)
    xmax, ymax = points.max(axis=0)

    cx = (xmin + xmax) / 2
    cy = (ymin + ymax) / 2

    side = int(max(xmax - xmin, ymax - ymin) * 1.8)

    x0 = int(cx - side / 2)
    y0 = int(cy - side / 2)

    crop = np.zeros((side, side, 3), dtype=np.uint8)

    h, w = image.shape[:2]

    sx1 = max(0, x0)
    sy1 = max(0, y0)
    sx2 = min(w, x0 + side)
    sy2 = min(h, y0 + side)

    crop[
        sy1-y0:sy2-y0,
        sx1-x0:sx2-x0
    ] = image[sy1:sy2, sx1:sx2]

    aligned = cv2.resize(crop, (SIZE, SIZE))

    points = (points - [x0, y0]) * (SIZE / side)

    return aligned, points.astype(np.float32)


p1 = detect_landmarks(img1)
p2 = detect_landmarks(img2)

face1, p1 = prepare_face(img1, p1)
face2, p2 = prepare_face(img2, p2)

# Add boundary points so the entire image is covered.

border = np.array([
    [0, 0], [128, 0], [255, 0],
    [255, 128], [255, 255],
    [128, 255], [0, 255], [0, 128]
], dtype=np.float32)

p1 = np.vstack([p1, border])
p2 = np.vstack([p2, border])

# -----------------------------------
# 4. CREATE DELAUNAY TRIANGULATION
# -----------------------------------

average_points = (p1 + p2) / 2

triangles = Delaunay(average_points).simplices


# -----------------------------------
# 5. MORPH TWO IMAGES
# -----------------------------------

def morph_images(img1, img2, p1, p2, alpha):

    h, w = img1.shape[:2]

    intermediate = (1-alpha)*p1 + alpha*p2

    warp1 = np.zeros_like(img1)
    warp2 = np.zeros_like(img2)

    for triangle in triangles:

        t1 = p1[triangle]
        t2 = p2[triangle]
        t = intermediate[triangle]

        r1 = cv2.boundingRect(t1)
        r2 = cv2.boundingRect(t2)
        r = cv2.boundingRect(t)

        x, y, rw, rh = r

        if rw <= 0 or rh <= 0:
            continue

        def local_points(points, rect):
            return points - np.array(rect[:2], dtype=np.float32)

        t1_local = local_points(t1, r1)
        t2_local = local_points(t2, r2)
        t_local = local_points(t, r)

        x1, y1, w1, h1 = r1
        x2, y2, w2, h2 = r2

        crop1 = img1[y1:y1+h1, x1:x1+w1]
        crop2 = img2[y2:y2+h2, x2:x2+w2]

        if crop1.size == 0 or crop2.size == 0:
            continue

        M1 = cv2.getAffineTransform(
            np.float32(t1_local),
            np.float32(t_local)
        )

        M2 = cv2.getAffineTransform(
            np.float32(t2_local),
            np.float32(t_local)
        )

        warped1 = cv2.warpAffine(
            crop1, M1, (rw, rh),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101
        )

        warped2 = cv2.warpAffine(
            crop2, M2, (rw, rh),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REFLECT_101
        )

        mask = np.zeros((rh, rw), dtype=np.uint8)

        cv2.fillConvexPoly(
            mask,
            np.int32(t_local),
            255,
            lineType=cv2.LINE_AA
        )

        region1 = warp1[y:y+rh, x:x+rw]
        region2 = warp2[y:y+rh, x:x+rw]

        region1[mask > 0] = warped1[mask > 0]
        region2[mask > 0] = warped2[mask > 0]

    # Blend both warped images.

    result = cv2.addWeighted(
        warp1, 1-alpha,
        warp2, alpha,
        0
    )

    return result


# -----------------------------------
# 6. GENERATE VIDEO FRAMES
# -----------------------------------

os.makedirs("frames", exist_ok=True)

FPS = 12
TOTAL_FRAMES = 48

frame_number = 0

# Hold the starting photograph.

for _ in range(6):
    cv2.imwrite(
        f"frames/frame_{frame_number:03d}.png",
        face1
    )
    frame_number += 1

# Gradually morph the faces.

for i in range(TOTAL_FRAMES):

    t = i / (TOTAL_FRAMES - 1)

    # Smooth easing for a natural transition.
    alpha = 3*t**2 - 2*t**3

    frame = morph_images(
        face1, face2, p1, p2, alpha
    )

    cv2.imwrite(
        f"frames/frame_{frame_number:03d}.png",
        frame
    )

    frame_number += 1

# Hold the ending photograph.

for _ in range(6):
    cv2.imwrite(
        f"frames/frame_{frame_number:03d}.png",
        face2
    )
    frame_number += 1


# -----------------------------------
# 7. CREATE AND COMPRESS VIDEO
# -----------------------------------

def encode_video(size, fps):

    for crf in [32, 36, 40, 44, 48, 51]:

        command = [
            "ffmpeg", "-y",
            "-framerate", str(fps),
            "-i", "frames/frame_%03d.png",
            "-vf", f"scale={size}:{size}",
            "-c:v", "libx264",
            "-preset", "veryslow",
            "-crf", str(crf),
            "-pix_fmt", "yuv420p",
            "-an",
            "-movflags", "+faststart",
            "face_morph.mp4"
        ]

        subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )

        file_size = os.path.getsize("face_morph.mp4")

        print(f"CRF {crf}: {file_size:,} bytes")

        if file_size < 95000:
            return True

    return False


# Try normal resolution first.

success = encode_video(240, FPS)

# Reduce resolution if necessary.

if not success:
    print("Trying smaller resolution...")
    success = encode_video(192, 10)

if success:
    print("Video successfully created!")
    print("File size:", os.path.getsize("face_morph.mp4"), "bytes")
else:
    print("Could not reach the size limit. Try reducing resolution.")
