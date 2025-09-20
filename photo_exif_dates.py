import argparse
import os
import sys
from datetime import datetime
from typing import Optional, List, Tuple

try:
    from PIL import Image, ExifTags, ImageDraw, ImageFont, ImageColor
except ImportError as e:
    print("Pillow is required. Please install it with: pip install Pillow", file=sys.stderr)
    raise


# Map EXIF tag ids to names once
EXIF_TAGS = {v: k for k, v in ExifTags.TAGS.items()}

# Common EXIF datetime tags (in priority order)
DATETIME_TAGS = [
    EXIF_TAGS.get("DateTimeOriginal"),  # 36867
    EXIF_TAGS.get("DateTimeDigitized"), # 36868
    EXIF_TAGS.get("DateTime"),          # 306
]

SUPPORTED_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".bmp"}


# ---------------- EXIF utilities ---------------- #

def extract_date_from_exif(file_path: str) -> Optional[str]:
    """
    Extract YYYY-MM-DD from EXIF datetime fields.
    Returns None if no usable EXIF date is found.
    """
    try:
        with Image.open(file_path) as im:
            # Prefer getexif (Pillow >= 7)
            exif = getattr(im, "getexif", None)
            if exif is None:
                return None
            exif_data = exif()
            if not exif_data:
                return None

            # Try known datetime tags in order
            for tag_id in DATETIME_TAGS:
                if tag_id is None:
                    continue
                value = exif_data.get(tag_id)
                if not value:
                    continue
                if isinstance(value, bytes):
                    try:
                        value = value.decode("utf-8", errors="ignore")
                    except Exception:
                        continue
                dt = _parse_exif_datetime(value)
                if dt is not None:
                    return dt.strftime("%Y-%m-%d")
    except Exception:
        # Any error (unsupported format, truncated file, etc.) means no date
        return None

    return None


def _parse_exif_datetime(dt_str: str) -> Optional[datetime]:
    s = (dt_str or "").strip().strip("\x00")
    if not s:
        return None

    for fmt in [
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y:%m:%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    candidates = [s[:10], s.replace(":", "-")[:10]]
    for cand in candidates:
        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y:%m:%d"]:
            try:
                return datetime.strptime(cand, fmt)
            except ValueError:
                continue

    return None


# ---------------- File discovery ---------------- #

def find_images(path: str, recursive: bool = True) -> List[str]:
    files = []
    if os.path.isfile(path):
        ext = os.path.splitext(path)[1].lower()
        if ext in SUPPORTED_EXTS:
            files.append(path)
    elif os.path.isdir(path):
        if recursive:
            for root, _, filenames in os.walk(path):
                for name in filenames:
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SUPPORTED_EXTS:
                        files.append(os.path.join(root, name))
        else:
            for name in os.listdir(path):
                fp = os.path.join(path, name)
                if os.path.isfile(fp):
                    ext = os.path.splitext(name)[1].lower()
                    if ext in SUPPORTED_EXTS:
                        files.append(fp)
    else:
        raise FileNotFoundError(f"Path not found: {path}")

    return files


# ---------------- Watermark drawing ---------------- #

POS_CHOICES = [
    "top-left", "top-center", "top-right",
    "center-left", "center", "center-right",
    "bottom-left", "bottom-center", "bottom-right",
]


def _load_font(font_path: Optional[str], font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # Try user-provided font first
    if font_path:
        try:
            return ImageFont.truetype(font_path, font_size)
        except Exception:
            print(f"[warn] Failed to load font from {font_path}, fallback to default.")
    # Try common bundled/system fonts
    for name in ("DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, font_size)
        except Exception:
            continue
    # Last resort
    return ImageFont.load_default()


def _parse_color(color_str: str) -> Tuple[int, int, int]:
    # Accept formats: #RRGGBB, rgb like 255,0,0, or named colors
    s = color_str.strip()
    try:
        if "," in s:
            parts = [int(x.strip()) for x in s.split(",")]
            if len(parts) >= 3:
                return tuple(parts[:3])  # type: ignore
        # PIL can parse #hex or named via ImageColor
        return ImageColor.getrgb(s)  # type: ignore
    except Exception:
        # Fallback to white if parsing fails
        return (255, 255, 255)


def _calc_position(pos: str, img_w: int, img_h: int, text_w: int, text_h: int, margin: int) -> Tuple[int, int]:
    if pos == "top-left":
        return margin, margin
    if pos == "top-center":
        return (img_w - text_w) // 2, margin
    if pos == "top-right":
        return img_w - text_w - margin, margin
    if pos == "center-left":
        return margin, (img_h - text_h) // 2
    if pos == "center":
        return (img_w - text_w) // 2, (img_h - text_h) // 2
    if pos == "center-right":
        return img_w - text_w - margin, (img_h - text_h) // 2
    if pos == "bottom-left":
        return margin, img_h - text_h - margin
    if pos == "bottom-center":
        return (img_w - text_w) // 2, img_h - text_h - margin
    if pos == "bottom-right":
        return img_w - text_w - margin, img_h - text_h - margin
    # default
    return img_w - text_w - margin, img_h - text_h - margin


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> Tuple[int, int]:
    # Prefer textbbox for accurate size
    try:
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        return right - left, bottom - top
    except Exception:
        return draw.textsize(text, font=font)


def annotate_image(src_path: str, text: str, out_path: str, font_size: int, color: str, opacity: int, position: str, margin: int, font_path: Optional[str] = None) -> bool:
    try:
        with Image.open(src_path) as im:
            # Work on RGBA for proper alpha compositing
            base = im.convert("RGBA")
            overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)

            font = _load_font(font_path, font_size)
            rgb = _parse_color(color)
            text_w, text_h = _text_size(draw, text, font)
            x, y = _calc_position(position, base.width, base.height, text_w, text_h, margin)

            draw.text((x, y), text, font=font, fill=(rgb[0], rgb[1], rgb[2], max(0, min(255, opacity))))

            out = Image.alpha_composite(base, overlay)

            # Ensure output directory exists
            os.makedirs(os.path.dirname(out_path), exist_ok=True)

            # Preserve original format as much as possible
            ext = os.path.splitext(out_path)[1].lower()
            if ext in {".jpg", ".jpeg"}:
                out.convert("RGB").save(out_path, format="JPEG", quality=95)
            elif ext in {".png"}:
                out.save(out_path, format="PNG")
            elif ext in {".webp"}:
                out.save(out_path, format="WEBP", quality=95)
            elif ext in {".tif", ".tiff"}:
                out.save(out_path, format="TIFF")
            else:
                # Default fallback
                out.save(out_path)
        return True
    except Exception as e:
        print(f"[error] Failed to annotate {src_path}: {e}", file=sys.stderr)
        return False


# ---------------- Paths for output ---------------- #

def resolve_root_and_output_dir(input_path: str) -> Tuple[str, str]:
    """Return (root_dir, output_root_dir) following rule:
    - If input is a directory: root_dir = input; output = root_dir/<basename(root_dir)>_watermark
    - If input is a file: root_dir = parent directory; output = root_dir/<basename(root_dir)>_watermark
    """
    if os.path.isdir(input_path):
        root_dir = os.path.abspath(input_path)
    else:
        root_dir = os.path.abspath(os.path.dirname(input_path))
    base_name = os.path.basename(root_dir)
    output_root = os.path.join(root_dir, f"{base_name}_watermark")
    return root_dir, output_root


def to_output_path(output_root: str, root_dir: str, file_path: str) -> str:
    rel = os.path.relpath(os.path.abspath(file_path), root_dir)
    return os.path.join(output_root, rel)


# ---------------- CLI ---------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Read EXIF shooting date (YYYY-MM-DD) from image(s) at the given path, "
            "then draw the date as a text watermark onto the image and save to a new folder."
        )
    )
    p.add_argument("path", help="Path to an image file or a directory containing images")
    p.add_argument("--non-recursive", action="store_true", help="If path is a directory, only scan the top level (do not recurse)")

    # Watermark options
    p.add_argument("--font-size", type=int, default=36, help="Font size in pixels for the watermark text (default: 36)")
    p.add_argument("--color", type=str, default="#FFFFFF", help="Font color (e.g., #RRGGBB, or 'red', or '255,0,0'). Default: #FFFFFF")
    p.add_argument("--opacity", type=int, default=200, help="Opacity of text fill, 0-255 (0=transparent, 255=solid). Default: 200")
    p.add_argument(
        "--position",
        type=str,
        choices=POS_CHOICES,
        default="bottom-right",
        help=(
            "Watermark position: " + ", ".join(POS_CHOICES) + ". Default: bottom-right"
        ),
    )
    p.add_argument("--margin", type=int, default=20, help="Margin (in pixels) from the edges. Default: 20")
    p.add_argument("--font-path", type=str, default=None, help="Path to a .ttf/.otf font file. If omitted, try DejaVuSans/Arial or fallback.")
    p.add_argument("--overwrite", action="store_true", help="Overwrite output files if they already exist")

    return p


def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    # discover files
    try:
        files = find_images(args.path, recursive=not args.non_recursive)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No supported images found.")
        sys.exit(0)

    root_dir, output_root = resolve_root_and_output_dir(args.path)

    processed = 0
    skipped_no_date = 0
    skipped_exists = 0
    failed = 0

    for fp in files:
        date_str = extract_date_from_exif(fp)
        if date_str is None:
            print(f"[skip] {fp} -> NO_EXIF_DATE")
            skipped_no_date += 1
            continue

        out_path = to_output_path(output_root, root_dir, fp)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        if (not args.overwrite) and os.path.exists(out_path):
            print(f"[skip] {fp} -> exists: {out_path}")
            skipped_exists += 1
            continue

        ok = annotate_image(
            src_path=fp,
            text=date_str,
            out_path=out_path,
            font_size=args.font_size,
            color=args.color,
            opacity=max(0, min(255, args.opacity)),
            position=args.position,
            margin=max(0, args.margin),
            font_path=args.font_path,
        )
        if ok:
            print(f"[ok]   {fp} -> {out_path} ({date_str})")
            processed += 1
        else:
            failed += 1

    print("\nSummary:")
    print(f"  processed: {processed}")
    print(f"  skipped (no EXIF date): {skipped_no_date}")
    print(f"  skipped (exists): {skipped_exists}")
    print(f"  failed: {failed}")


if __name__ == "__main__":
    main()