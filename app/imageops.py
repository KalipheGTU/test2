import math, shutil, time
import numpy as np
from PIL import Image, ImageFile, ImageOps

ImageFile.LOAD_TRUNCATED_IMAGES = True      # accepte les JPEG tronqués (scans abîmés)
Image.MAX_IMAGE_PIXELS = 400_000_000
HIGH = {"I;16", "I;16L", "I;16B", "I;16N", "I", "F"}     # images 16/32 bits : MNT, altitudes

def src_path(d):
    p = d / "source.png"
    return p if p.exists() else d / "source.jpg"

def rm(p):
    """Suppression tolérante (Windows refuse de supprimer un fichier encore ouvert)."""
    try: p.unlink()
    except FileNotFoundError: pass
    except PermissionError:
        try: p.rename(p.with_name(p.name + f".old{time.time_ns()}"))
        except Exception: pass

def open_loaded(p):
    """Ouvre ET décode complètement l'image : le fichier est refermé tout de suite (indispensable sous Windows)."""
    im = Image.open(p); im.load(); return im

def size_of(p):
    with Image.open(p) as im: return im.size

def load(d): return open_loaded(src_path(d))

def import_image(raw, d):
    """Importe n'importe quelle image. Retourne un dict d'infos (dem={...} pour les images 16/32 bits)."""
    with Image.open(raw) as im0:
        im0.load(); fmt, im = im0.format, im0.copy()
    info = {}
    if im.mode in HIGH:                                   # ---- MNT / altitudes : on garde les valeurs brutes
        arr = np.asarray(im).astype(np.float32)
        nod = ~np.isfinite(arr) | (arr <= -9000)
        vals = arr[~nod]
        lo, hi = (np.percentile(vals, [2, 98]) if vals.size else (0.0, 1.0))
        if hi <= lo: hi = lo + 1.0
        g8 = (np.clip((arr - lo) / (hi - lo), 0, 1) * 255).astype(np.uint8)
        rgba = np.dstack([g8, g8, g8, np.where(nod, 0, 255).astype(np.uint8)])
        Image.fromarray(rgba, "RGBA").save(d / "orig.png"); shutil.copyfile(d / "orig.png", d / "source.png")
        arr[nod] = np.nan; np.save(d / "raw.npy", arr); np.save(d / "orig_raw.npy", arr)
        info["dem"] = {"min": float(vals.min()) if vals.size else 0.0, "max": float(vals.max()) if vals.size else 0.0,
                       "p2": float(lo), "p98": float(hi), "src_mode": im.mode}
        return info
    try: orient = im.getexif().get(274, 1)
    except Exception: orient = 1
    keep = fmt == "JPEG" and im.mode == "RGB" and orient in (1, None)
    if keep:                                              # JPEG d'origine conservé tel quel (aucune recompression)
        shutil.copyfile(raw, d / "orig.jpg"); shutil.copyfile(raw, d / "source.jpg"); return info
    im = ImageOps.exif_transpose(im)
    alpha = "A" in im.mode or im.mode in ("LA", "PA", "RGBa", "La") or (im.mode == "P" and "transparency" in im.info)
    im = im.convert("RGBA" if alpha else "RGB")
    if fmt == "JPEG":
        im.save(d / "orig.jpg", quality=95); shutil.copyfile(d / "orig.jpg", d / "source.jpg")
    else:
        im.save(d / "orig.png"); shutil.copyfile(d / "orig.png", d / "source.png")
    return info

def save_cur(d, im):
    for f in ("source.png", "source.jpg"): rm(d / f)
    clean_previews(d)
    if im.mode == "RGBA": im.save(d / "source.png")
    else: im.convert("RGB").save(d / "source.jpg", quality=95)

def clean_previews(d):
    for pat in ("prev_small*.png", "preview*.png"):
        for f in d.glob(pat): rm(f)

def crop_box(w, h, box):
    x0, y0 = max(0, int(round(box[0]))), max(0, int(round(box[1])))
    x1, y1 = min(w, int(round(box[2]))), min(h, int(round(box[3])))
    if x1 - x0 < 8 or y1 - y0 < 8: raise ValueError("zone de rognage trop petite")
    return x0, y0, x1, y1

def crop(im, box, gcps):
    x0, y0, x1, y1 = crop_box(*im.size, box)
    return im.crop((x0, y0, x1, y1)), [{**g, "px": g["px"] - x0, "py": g["py"] - y0} for g in gcps]

def rotate(im, angle, gcps):
    """angle en degrés, positif = anti-horaire. Fond transparent."""
    W, H = im.size
    im2 = im.convert("RGBA").rotate(angle, expand=True, resample=Image.BICUBIC)
    W2, H2 = im2.size; a = math.radians(angle); c, s = math.cos(a), math.sin(a)
    out = []
    for g in gcps:
        dx, dy = g["px"] - W / 2, g["py"] - H / 2
        out.append({**g, "px": W2 / 2 + dx * c + dy * s, "py": H2 / 2 - dx * s + dy * c})
    return im2, out

def crop_raw(d, box):
    raw = np.load(d / "raw.npy"); h, w = raw.shape
    x0, y0, x1, y1 = crop_box(w, h, box); np.save(d / "raw.npy", raw[y0:y1, x0:x1])

def rotate_raw(d, angle):
    raw = np.load(d / "raw.npy"); ok = np.isfinite(raw)
    r = Image.fromarray(np.where(ok, raw, -99999.0).astype(np.float32), "F").rotate(angle, expand=True, resample=Image.BILINEAR)
    m = Image.fromarray(ok.astype(np.float32), "F").rotate(angle, expand=True, resample=Image.BILINEAR)
    np.save(d / "raw.npy", np.where(np.asarray(m) > 0.99, np.asarray(r), np.nan).astype(np.float32))

def remove_colors(im, colors, tol):
    a = np.asarray(im.convert("RGBA")).copy(); rgb = a[..., :3].astype(int)
    m = np.zeros(a.shape[:2], bool)
    for c in colors: m |= np.abs(rgb - np.array(c[:3])).max(axis=2) <= tol
    a[m, 3] = 0
    return Image.fromarray(a, "RGBA")

def reset(d):
    for f in ("source.png", "source.jpg"): rm(d / f)
    clean_previews(d)
    o = d / "orig.jpg" if (d / "orig.jpg").exists() else d / "orig.png"
    shutil.copyfile(o, d / ("source.jpg" if o.suffix == ".jpg" else "source.png"))
    if (d / "orig_raw.npy").exists(): shutil.copyfile(d / "orig_raw.npy", d / "raw.npy")

def prev_small(d, rev=0, side=2048):
    """Copie réduite de l'image courante pour l'aperçu. Le nom change à chaque modification (jamais d'ancienne version)."""
    src = src_path(d); p = d / f"prev_small_{rev}.png"
    w, h = size_of(src)
    if not p.exists():
        for f in d.glob("prev_small*.png"): rm(f)
        im = Image.open(src); f = min(1.0, side / max(w, h))
        if f < 1 and im.format == "JPEG": im.draft("RGB", (round(w * f), round(h * f)))
        im.load(); im = im.convert("RGBA" if im.mode == "RGBA" else "RGB")
        if f < 1: im = im.resize((round(w * f), round(h * f)), Image.LANCZOS)
        im.save(p)
    return p, size_of(p)[0] / w
