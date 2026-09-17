"""Image copying and OGP (Open Graph) image generation."""

import shutil
from pathlib import Path

from PIL import Image, ImageOps

from . import config


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif", ".pdf"}

# OGP image: Facebook/X recommended 1.91:1, 1200x630 is the sweet spot.
OGP_SIZE = (1200, 630)
OGP_QUALITY = 85
OGP_FILENAME = "og-image.jpg"


def copy_images(src_dir, out_dir, lang="ja"):
    """Copy article assets (images, PDFs) from `src_dir` (the per-article
    folder containing `ja.md`/`en.md`) into `out_dir`.

    Filename convention for language-specific assets:
      - `<name>.<ext>`        — JA-only or shared (copied to both)
      - `en-<name>.<ext>`     — EN-only (copied only to EN target)

    JA build skips `en-`-prefixed files so EN-only PDFs etc. don't leak
    into the JA output. EN build copies everything so it can fall back to
    shared assets when an EN-specific version doesn't exist.
    """
    for f in src_dir.iterdir():
        if not f.is_file():
            continue
        if f.suffix.lower() not in IMAGE_EXTS:
            continue
        if lang == "ja" and f.name.startswith("en-"):
            continue
        shutil.copy2(f, out_dir / f.name)


def generate_ogp_image(src_path, out_path, size=OGP_SIZE, quality=OGP_QUALITY):
    """Center-crop and resize an image to OGP dimensions (1200x630 JPEG).

    Honours EXIF orientation so iPhone portrait photos come out upright.
    Returns True on success, False if src does not exist or cannot be opened.
    """
    src_path = Path(src_path)
    out_path = Path(out_path)
    if not src_path.exists():
        return False

    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")

        target_w, target_h = size
        src_w, src_h = img.size
        target_ratio = target_w / target_h
        src_ratio = src_w / src_h

        # Center-crop to target aspect ratio, then resize
        if src_ratio > target_ratio:
            new_w = int(round(src_h * target_ratio))
            left = (src_w - new_w) // 2
            img = img.crop((left, 0, left + new_w, src_h))
        elif src_ratio < target_ratio:
            new_h = int(round(src_w / target_ratio))
            top = (src_h - new_h) // 2
            img = img.crop((0, top, src_w, top + new_h))

        img = img.resize(size, Image.LANCZOS)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, "JPEG", quality=quality, optimize=True, progressive=True)
    return True


def resolve_og_image(meta, out_dir, public_base_url):
    """Decide og_image URL and (optionally) generate the OGP JPEG.

    Precedence:
      1. `og_image` frontmatter starting with http(s):// → used verbatim (manual override)
      2. `hero_image` frontmatter → crop/resize to OGP_FILENAME in out_dir,
         return public URL pointing at it
      3. Fallback → config.DEFAULT_OG_IMAGE
    """
    explicit = meta.get("og_image", "").strip()
    if explicit.startswith("http://") or explicit.startswith("https://"):
        return explicit

    hero = meta.get("hero_image", "").strip()
    if hero:
        src = Path(meta["_source_dir"]) / hero
        if src.exists():
            generate_ogp_image(src, out_dir / OGP_FILENAME)
            return f"{public_base_url.rstrip('/')}/{OGP_FILENAME}"

    return config.DEFAULT_OG_IMAGE
