import cv2, numpy as np, mediapipe as mp, os
from scipy.spatial import Delaunay
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

S = 400         # small picture = small video file
N = 60           # frames in the morph

detector = vision.FaceLandmarker.create_from_options(
    vision.FaceLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path="face_landmarker.task"),
        num_faces=1,
    )
)

def get_landmarks(img):
    """Find the points on the face (eyes, nose, lips, jawline...)."""
    rgb = np.ascontiguousarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    res = detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    if not res.face_landmarks:
        raise SystemExit("No face found in a photo")
    h, w = img.shape[:2]
    return np.array([(p.x * w, p.y * h) for p in res.face_landmarks[0]], np.float32)

def crop_face(img):
    """Cut a square around the face so both faces are the same size and position."""
    pts = get_landmarks(img)
    x0, y0 = pts.min(0); x1, y1 = pts.max(0)
    cx, cy = (x0+x1)/2, (y0+y1)/2
    half = max(x1-x0, y1-y0) * 0.8
    pad = int(half*2) + 10
    big = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    cx += pad; cy += pad
    crop = big[int(cy-half):int(cy+half), int(cx-half):int(cx+half)]
    return cv2.resize(crop, (S, S), interpolation=cv2.INTER_AREA)

def warp_tri(src, dst, t_src, t_dst):
    """Stretch one small triangle of the source onto its new spot."""
    r1 = cv2.boundingRect(np.float32([t_src])); r2 = cv2.boundingRect(np.float32([t_dst]))
    a = np.float32([[p[0]-r1[0], p[1]-r1[1]] for p in t_src])
    b = np.float32([[p[0]-r2[0], p[1]-r2[1]] for p in t_dst])
    mask = np.zeros((r2[3], r2[2], 3), np.float32)
    cv2.fillConvexPoly(mask, np.int32(b), (1, 1, 1), 16, 0)
    patch = src[r1[1]:r1[1]+r1[3], r1[0]:r1[0]+r1[2]]
    M = cv2.getAffineTransform(a, b)
    warped = cv2.warpAffine(patch, M, (r2[2], r2[3]), borderMode=cv2.BORDER_REFLECT_101)
    roi = dst[r2[1]:r2[1]+r2[3], r2[0]:r2[0]+r2[2]]
    roi[:] = roi*(1-mask) + warped*mask

def morph(a, b, pa, pb, tri, t):
    pm = (1-t)*pa + t*pb                      # in-between face shape
    wa = np.zeros_like(a); wb = np.zeros_like(b)
    for s in tri:
        warp_tri(a, wa, pa[s], pm[s])
        warp_tri(b, wb, pb[s], pm[s])
    return np.uint8((1-t)*wa + t*wb)          # blend the colors

a = crop_face(cv2.imread("arthur.jpg")); b = crop_face(cv2.imread("rachel.jpg"))
pa = np.clip(get_landmarks(a), 0, S-1); pb = np.clip(get_landmarks(b), 0, S-1)

# pin the picture's corners and edges too, so the background doesn't tear
m = S-1
edge = np.float32([[0,0],[m/2,0],[m,0],[0,m/2],[m,m/2],[0,m],[m/2,m],[m,m]])
pa = np.vstack([pa, edge]); pb = np.vstack([pb, edge])

tri = Delaunay((pa+pb)/2).simplices           # carve the face into triangles
a = a.astype(np.float32); b = b.astype(np.float32)

ease = lambda t: t*t*(3-2*t)                  # starts/ends gently = looks smoother
ts = [0]*8 + [ease(i/(N-1)) for i in range(N)] + [1]*8   # pause on each face

os.makedirs("frames", exist_ok=True)
for i, t in enumerate(ts):
    cv2.imwrite(f"frames/{i:03d}.png", morph(a, b, pa, pb, tri, t))
print("done:", len(ts), "frames")