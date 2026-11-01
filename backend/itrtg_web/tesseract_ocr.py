from __future__ import annotations

import asyncio
import csv
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from itrtg_web.ocr import OcrLine

OCR_TIMEOUT_SECONDS = 15
MISSING_TESSERACT = (
    "Screenshot OCR requires Tesseract with English language data. "
    "On Windows, run start.cmd to install it, or set ITRTG_TESSERACT_PATH to tesseract.exe. "
    "On other platforms, install Tesseract with English language data or use the Docker image."
)


def resolve_tesseract_binary() -> str | None:
    configured_path = os.environ.get("ITRTG_TESSERACT_PATH")
    if configured_path:
        binary = Path(configured_path).expanduser()
        if not binary.is_file():
            raise RuntimeError(
                "ITRTG_TESSERACT_PATH does not point to a Tesseract executable. "
                "Set it to tesseract.exe, or run start.cmd to install Tesseract with English language data."
            )
        return str(binary)
    if binary := shutil.which("tesseract"):
        return binary
    if sys.platform == "win32":
        for variable, relative_path in (
            ("ProgramFiles", "Tesseract-OCR/tesseract.exe"),
            ("ProgramFiles(x86)", "Tesseract-OCR/tesseract.exe"),
            ("LOCALAPPDATA", "Programs/Tesseract-OCR/tesseract.exe"),
            ("LOCALAPPDATA", "Tesseract-OCR/tesseract.exe"),
        ):
            root = os.environ.get(variable)
            if root and (binary := Path(root) / relative_path).is_file():
                return str(binary)
    return None


def parse_tesseract_tsv(tsv_text: str) -> list[OcrLine]:
    from itrtg_web.ocr import _split_ocr_words

    reader = csv.DictReader(io.StringIO(tsv_text), delimiter="\t", quoting=csv.QUOTE_NONE)
    required = {"level", "page_num", "block_num", "par_num", "line_num", "left", "top", "width", "height", "conf", "text"}
    if not required.issubset(reader.fieldnames or []):
        raise RuntimeError("Tesseract returned an invalid OCR result.")
    groups = {}
    for row in reader:
        try:
            confidence = float(row["conf"])
            text = row["text"].strip()
            left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            if int(row["level"]) != 5 or not math.isfinite(confidence) or confidence < 0 or not text or width <= 0 or height <= 0:
                continue
            key = tuple(int(row[field]) for field in ("page_num", "block_num", "par_num", "line_num"))
        except (KeyError, TypeError, ValueError):
            continue
        word = SimpleNamespace(text=text, bounding_rect=SimpleNamespace(x=left, y=top, width=width, height=height))
        groups.setdefault(key, []).append(word)
    return [line for words in groups.values() for line in _split_ocr_words(words)]


def _recognize_tesseract(image_path: Path) -> tuple[list[OcrLine], int, int]:
    from PIL import Image, ImageChops, ImageOps, UnidentifiedImageError

    binary = resolve_tesseract_binary()
    if binary is None:
        raise RuntimeError(MISSING_TESSERACT)
    temporary_path: Path | None = None
    try:
        try:
            with Image.open(image_path) as source:
                image = source.convert("RGB")
            prepared_path = image_path
            red, green, blue = image.split()
            minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
            maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            span = ImageChops.subtract(maximum, minimum)
            # Game text is light and neutral; remove the colourful scene behind it.
            # Already monochrome field sheets and timer crops retain their pixels.
            if span.getextrema()[1] > 60:
                brightness = minimum.point(lambda pixel: 255 if pixel >= 140 else 0)
                neutral = span.point(lambda pixel: 255 if pixel <= 60 else 0)
                image = ImageOps.invert(ImageChops.multiply(brightness, neutral))
                image = image.resize((3000, max(1, round(image.height * 3000 / image.width))), Image.Resampling.LANCZOS)
                with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as output:
                    temporary_path = Path(output.name)
                image.save(temporary_path, format="PNG")
                prepared_path = temporary_path
            width, height = image.size
        except (OSError, UnidentifiedImageError, ValueError) as exc:
            raise ValueError("Could not read the image.") from exc
        try:
            result = subprocess.run(
                [binary, str(prepared_path), "stdout", "-l", "eng", "--psm", "6", "tsv"],
                capture_output=True,
                check=False,
                timeout=OCR_TIMEOUT_SECONDS,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(MISSING_TESSERACT) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("Screenshot OCR timed out. Try a smaller or clearer screenshot.") from exc
        except OSError as exc:
            raise RuntimeError("Could not start Tesseract OCR.") from exc
        if result.returncode != 0:
            raise RuntimeError("Screenshot OCR failed. Check that Tesseract and its English language data are installed.")
        return parse_tesseract_tsv(result.stdout.decode("utf-8", errors="replace")), width, height
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def extract_lines_tesseract(image_path: Path) -> tuple[list[OcrLine], int, int]:
    return await asyncio.to_thread(_recognize_tesseract, image_path)
