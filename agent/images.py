from pathlib import Path

from PIL import Image, ImageOps


def to_webp(src: Path, dest: Path, max_side: int = 1600, quality: int = 82):
    """Resize (keep ratio) and convert to WebP. Returns (before_kb, after_kb, (w, h))."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as im:
        im = ImageOps.exif_transpose(im)
        im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
        im.thumbnail((max_side, max_side), Image.LANCZOS)
        im.save(dest, "WEBP", quality=quality, method=6)
        size = im.size
    return src.stat().st_size // 1024, dest.stat().st_size // 1024, size
