import argparse
import os
import sys
from datetime import datetime
from typing import Optional, List

try:
    from PIL import Image, ExifTags
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
                # Typical EXIF format: "YYYY:MM:DD HH:MM:SS"
                # Some cameras may vary; we try to be flexible
                dt = _parse_exif_datetime(value)
                if dt is not None:
                    return dt.strftime("%Y-%m-%d")
    except Exception:
        # Any error (unsupported format, truncated file, etc.) means no date
        return None

    return None


def _parse_exif_datetime(dt_str: str) -> Optional[datetime]:
    # Normalize: strip nulls and whitespace
    s = (dt_str or "").strip().strip("\x00")
    if not s:
        return None

    # Most common: "YYYY:MM:DD HH:MM:SS"
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

    # Fallback: try to salvage date portion if string contains date-like prefix
    # e.g., "2023:07:15 00:00:00\x00\x00"
    # Extract first 10-characters patterns
    candidates = [s[:10], s.replace(":", "-")[:10]]
    for cand in candidates:
        for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y:%m:%d"]:
            try:
                return datetime.strptime(cand, fmt)
            except ValueError:
                continue

    return None


def find_images(path: str, recursive: bool = True) -> List[str]:
    SUPPORTED_EXTS = {".jpg", ".jpeg", ".tif", ".tiff", ".png", ".webp", ".bmp"}

    files = []
    if os.path.isfile(path):
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


def main():
    parser = argparse.ArgumentParser(
        description="Read EXIF shooting date (YYYY-MM-DD) from image(s) at the given path"
    )
    parser.add_argument(
        "path",
        help="Path to an image file or a directory containing images"
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="If path is a directory, only scan the top level (do not recurse)"
    )
    args = parser.parse_args()

    try:
        files = find_images(args.path, recursive=not args.non_recursive)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if not files:
        print("No supported images found.")
        sys.exit(0)

    for fp in files:
        date_str = extract_date_from_exif(fp)
        if date_str is None:
            print(f"{fp}\tNO_EXIF_DATE")
        else:
            print(f"{fp}\t{date_str}")


if __name__ == "__main__":
    main()