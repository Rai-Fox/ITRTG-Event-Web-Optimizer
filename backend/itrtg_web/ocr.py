from __future__ import annotations

import os
import sys
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from math import ceil
from pathlib import Path
from statistics import median
from typing import Literal

from itrtg_web.models import OcrParseResult, RESOURCE_LABELS


OcrEngineId = Literal["tesseract", "windows"]


def available_ocr_engines() -> list[dict[str, str]]:
    engines = [{"id": "tesseract", "name": "Tesseract"}]
    if sys.platform == "win32":
        engines.append({"id": "windows", "name": "Windows OCR"})
    return engines


@dataclass(frozen=True)
class OcrLine:
    text: str
    left: float
    top: float
    width: float
    height: float

    @property
    def center_x(self) -> float:
        return self.left + self.width / 2.0

    @property
    def center_y(self) -> float:
        return self.top + self.height / 2.0


@dataclass(frozen=True)
class GridLayout:
    left_title_center: float
    top_title_center: float
    column_pitch: float
    row_pitch: float


def _detect_grid_layout(lines: list[OcrLine]) -> GridLayout | None:
    """Locate the grid by resource headings, independently of screenshot borders."""
    names = {re.sub(r"[^a-z]", "", name.lower()): index for index, name in enumerate(RESOURCE_LABELS)}
    titles = {}
    for line in lines:
        name = re.sub(r"[^a-z]", "", line.text.lower())
        if name in names:
            titles[names[name]] = line
    rows = {row: median(line.center_y for index, line in titles.items() if index // 3 == row)
            for row in range(4) if any(index // 3 == row for index in titles)}
    columns = {column: median(line.center_x for index, line in titles.items() if index % 3 == column)
               for column in range(3) if any(index % 3 == column for index in titles)}
    if len(rows) < 2 or len(columns) < 2:
        return None
    row_pitch = median((rows[b] - rows[a]) / (b - a) for a in rows for b in rows if b > a)
    column_pitch = median((columns[b] - columns[a]) / (b - a) for a in columns for b in columns if b > a)
    if row_pitch <= 0 or column_pitch <= 0:
        return None
    top = median(value - index * row_pitch for index, value in rows.items())
    left = median(value - index * column_pitch for index, value in columns.items())
    if any(abs(line.center_y - (top + index // 3 * row_pitch)) > row_pitch * 0.2
           or abs(line.center_x - (left + index % 3 * column_pitch)) > column_pitch * 0.2
           for index, line in titles.items()):
        return None
    return GridLayout(left, top, column_pitch, row_pitch)


FIELD_PATTERNS = {
    "level": re.compile(r"(?:l\W*e\W*v\W*e\W*[l1i]|leve1|lvl)", re.IGNORECASE),
    "speed": re.compile(r"(?:s\W*p\W*e\W*e\W*d|p\W*e\W*e\W*d|spd)", re.IGNORECASE),
    "count": re.compile(r"(?:c\W*o\W*u\W*n\W*t|cunt|ount)", re.IGNORECASE),
}


def _field_match(text: str) -> tuple[str, re.Match[str]] | None:
    for field, pattern in FIELD_PATTERNS.items():
        if match := pattern.search(text):
            return field, match
    return None


def _parse_small_integer(text: str) -> int | None:
    match = re.fullmatch(r"[0-9IOoil](?:\s*[0-9IOoil])*", text.strip().lstrip(":").strip())
    if not match:
        return None
    normalized = re.sub(r"\s+", "", match.group(0)).translate(
        str.maketrans({"I": "1", "i": "1", "l": "1", "O": "0", "o": "0"})
    )
    return int(normalized) if normalized.isdigit() else None


def _parse_count(text: str) -> int | None:
    raw = text.strip().lstrip(":").strip()
    if not re.fullmatch(r"[0-9IOoil][0-9IOoil,\s]*(?:\.[0-9IOoil]+)?(?:\s*[Ee]\s*[+-]?\s*[0-9IOoil]+)?", raw):
        return None
    raw = re.sub(r"\s+", "", raw).translate(
        str.maketrans({"I": "1", "i": "1", "l": "1", "O": "0", "o": "0"})
    )
    # A partial OCR read such as "1,5" must not silently become 15.
    if not re.fullmatch(r"(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?(?:[Ee][+-]?\d+)?", raw):
        return None
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    if not value.is_finite() or value < 0 or value != value.to_integral_value():
        return None
    return int(value)


def _parse_line(line: OcrLine) -> tuple[str, int] | None:
    field_match = _field_match(line.text)
    if not field_match:
        return None
    if sum(len(list(pattern.finditer(line.text))) for pattern in FIELD_PATTERNS.values()) != 1:
        # A bounding box for multiple fields cannot locate each value safely.
        return None
    field, label = field_match
    remainder = line.text[label.end():].strip().lstrip(":;=").strip()
    if field != "count":
        value = _parse_small_integer(remainder)
        return (field, value) if value is not None else None

    value = _parse_count(remainder)
    return (field, value) if value is not None else None


def _standalone_number(text: str) -> str | None:
    stripped = text.strip().strip(":.;")
    if _parse_count(stripped) is not None:
        return stripped
    return None


def _merge_orphan_numbers(lines: list[OcrLine], image_width: int) -> list[OcrLine]:
    """Join OCR lines such as ``Level:`` and a nearby standalone ``2``."""
    merged = list(lines)
    used_orphans: set[int] = set()
    for index, line in enumerate(lines):
        if not _field_match(line.text) or _parse_line(line) is not None:
            continue
        column = min(2, max(0, int(line.center_x * 3 / image_width))) if image_width > 0 else 0
        candidates: list[tuple[float, int, OcrLine, str]] = []
        for orphan_index, orphan in enumerate(lines):
            if orphan_index == index or orphan_index in used_orphans:
                continue
            number = _standalone_number(orphan.text)
            if number is None:
                continue
            orphan_column = min(2, max(0, int(orphan.center_x * 3 / image_width))) if image_width > 0 else 0
            vertical_distance = abs(orphan.center_y - line.center_y)
            if orphan.left < line.left + line.width - max(line.height, orphan.height) * 0.25:
                continue
            if orphan.left - (line.left + line.width) > max(line.height, orphan.height) * 3:
                continue
            if orphan_column != column or vertical_distance > max(line.height, orphan.height) * 0.75:
                continue
            candidates.append((vertical_distance, orphan_index, orphan, number))
        if not candidates:
            continue
        _, orphan_index, orphan, number = min(candidates, key=lambda candidate: candidate[0])
        used_orphans.add(orphan_index)
        left = min(line.left, orphan.left)
        top = min(line.top, orphan.top)
        right = max(line.left + line.width, orphan.left + orphan.width)
        bottom = max(line.top + line.height, orphan.top + orphan.height)
        merged[index] = OcrLine(f"{line.text} {number}", left, top, right - left, bottom - top)
    return [line for index, line in enumerate(merged) if index not in used_orphans]


def _split_ocr_words(words) -> list[OcrLine]:
    """Keep adjacent columns separate even when Windows returns one OCR line."""
    groups = []
    group = []
    for word in sorted(words, key=lambda item: item.bounding_rect.x):
        box = word.bounding_rect
        if group:
            previous = group[-1].bounding_rect
            gap = box.x - previous.x - previous.width
            next_field = _field_match(word.text) and any(_field_match(item.text) for item in group)
            if gap > max(box.height, previous.height) * 2 or next_field:
                groups.append(group)
                group = []
        group.append(word)
    if group:
        groups.append(group)
    lines = []
    for group in groups:
        left = min(word.bounding_rect.x for word in group)
        top = min(word.bounding_rect.y for word in group)
        right = max(word.bounding_rect.x + word.bounding_rect.width for word in group)
        bottom = max(word.bounding_rect.y + word.bounding_rect.height for word in group)
        lines.append(OcrLine(" ".join(word.text for word in group), left, top, right - left, bottom - top))
    return lines


def parse_ocr_lines(lines: list[OcrLine], image_width: int, image_height: int) -> OcrParseResult:
    values: dict[str, list[int | None]] = {
        "level": [None] * 12,
        "speed": [None] * 12,
        "count": [None] * 12,
    }
    warnings: list[str] = []
    normalized_lines = _merge_orphan_numbers(lines, image_width)
    layout = _detect_grid_layout(normalized_lines)
    matches = [(line, parsed) for line in normalized_lines if (parsed := _parse_line(line)) is not None]

    if image_width <= 0 or image_height <= 0 or len(matches) < 4:
        warnings.append("Could not detect the 12-resource grid.")
    else:
        ordered = sorted(matches, key=lambda item: item[0].center_y)
        gaps = [
            (ordered[index + 1][0].center_y - ordered[index][0].center_y, index)
            for index in range(len(ordered) - 1)
        ]
        row_gaps = sorted(gaps, reverse=True)[:3]
        if layout is None and (len(row_gaps) != 3 or min(gap for gap, _ in row_gaps) < image_height * 0.05):
            warnings.append("Could not reliably split the cards into four rows.")
        else:
            cut_indexes = sorted(index for _, index in row_gaps)
            row_boundaries = [
                (ordered[index][0].center_y + ordered[index + 1][0].center_y) / 2.0
                for index in cut_indexes
            ]
            duplicates: set[tuple[str, int]] = set()
            for line, (field, value) in matches:
                if layout:
                    row = round((line.center_y - layout.top_title_center) / layout.row_pitch - 0.4)
                    column = round((line.center_x - layout.left_title_center) / layout.column_pitch)
                    if not (0 <= row < 4 and 0 <= column < 3):
                        continue
                else:
                    row = sum(line.center_y > boundary for boundary in row_boundaries)
                    column = min(2, max(0, int(line.center_x * 3 / image_width)))
                resource_index = row * 3 + column
                key = field, resource_index
                if values[field][resource_index] is not None:
                    values[field][resource_index] = None
                    duplicates.add(key)
                elif key not in duplicates:
                    values[field][resource_index] = value

            for field, resource_index in sorted(duplicates):
                warnings.append(f"Ambiguous {field} for {RESOURCE_LABELS[resource_index]}.")

    labels = {"level": "Level", "speed": "Speed", "count": "Count"}
    for field, field_values in values.items():
        for resource_index, value in enumerate(field_values):
            if value is None:
                warnings.append(f"Missing {labels[field]} for {RESOURCE_LABELS[resource_index]}.")

    complete = all(value is not None for field_values in values.values() for value in field_values)
    return OcrParseResult(
        levels=values["level"],
        speeds=values["speed"],
        counts=values["count"],
        warnings=warnings,
        complete=complete,
    )


async def extract_lines_windows_ocr(image_path: Path) -> tuple[list[OcrLine], int, int]:
    if sys.platform != "win32":
        raise RuntimeError("Screenshot OCR is only available on Windows. Enter game values manually on this platform.")
    try:
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.globalization import Language
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError as exc:
        raise RuntimeError("Windows OCR dependencies are missing. Install winsdk to enable OCR import.") from exc

    try:
        path = os.path.abspath(str(image_path))
        file = await StorageFile.get_file_from_path_async(path)
        stream = await file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_language(Language("en-US")) or OcrEngine.try_create_from_user_profile_languages()
        if not engine:
            raise RuntimeError("Could not start Windows OCR for the installed user languages.")
        result = await engine.recognize_async(bitmap)
    except RuntimeError:
        raise
    except Exception as exc:
        raise ValueError("Could not read the image.") from exc

    lines: list[OcrLine] = []
    for result_line in result.lines:
        lines.extend(_split_ocr_words(result_line.words))
    return lines, int(decoder.pixel_width), int(decoder.pixel_height)


async def extract_lines_ocr(
    image_path: Path, engine: OcrEngineId = "tesseract",
) -> tuple[list[OcrLine], int, int]:
    if engine == "windows":
        if sys.platform != "win32":
            raise ValueError("Windows OCR is only available on Windows.")
        return await extract_lines_windows_ocr(image_path)
    if engine != "tesseract":
        raise ValueError(f"Unknown OCR engine: {engine}")
    from itrtg_web.tesseract_ocr import extract_lines_tesseract

    return await extract_lines_tesseract(image_path)


def _prepare_ocr_image(
    image_path: Path,
    *,
    crop: tuple[int, int, int, int] | None = None,
    scale: int | None = 2,
    threshold: int | None = 175,
) -> Path:
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError("Pillow is required to prepare the image.") from exc

    output_path: Path | None = None
    try:
        with Image.open(image_path) as source:
            source.load()
            image = source.convert("RGB")
        if crop is not None:
            image = image.crop(crop)
        if scale is None:
            scale = max(2, ceil(2000 / image.width))
        if threshold is not None:
            image = ImageOps.autocontrast(image.convert("L"))
        image = image.resize((image.width * scale, image.height * scale), Image.Resampling.LANCZOS)
        if threshold is not None:
            image = image.point(lambda pixel: 255 if pixel >= threshold else 0)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as output:
            output_path = Path(output.name)
        image.save(output_path, format="PNG")
        return output_path
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        if output_path is not None:
            output_path.unlink(missing_ok=True)
        raise ValueError("Could not read the image.") from exc


def _card_fields_crop(width: int, height: int, resource_index: int, layout: GridLayout) -> tuple[int, int, int, int]:
    row, column = divmod(resource_index, 3)
    center_x = layout.left_title_center + column * layout.column_pitch
    center_y = layout.top_title_center + row * layout.row_pitch
    return (
        max(0, round(center_x - layout.column_pitch * 0.31)),
        max(0, round(center_y + layout.row_pitch * 0.08)),
        min(width, round(center_x + layout.column_pitch * 0.37)),
        min(height, round(center_y + layout.row_pitch * 0.70)),
    )


def _prepare_field_sheet(image_path: Path, layout: GridLayout) -> Path:
    """Give narrow value glyphs label context; never synthesize numeric values."""
    from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

    with Image.open(image_path) as source:
        image = source.convert("RGB")
    sheet = Image.new("RGB", (1800, 1080), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=44)
    for index in range(12):
        row, column = divmod(index, 3)
        card_box = _card_fields_crop(image.width, image.height, index, layout)
        card = image.crop(card_box)
        for field_index, label in enumerate(("Level:", "Speed:", "Count:")):
            label_x, label_y = field_index * 600 + 10, index * 90 + 15
            draw.text((label_x, label_y), label, fill="black", font=font)
            x = round(layout.left_title_center + (column - 0.067) * layout.column_pitch)
            y = round(layout.top_title_center + (row + 0.118 + field_index * 0.203) * layout.row_pitch)
            width = max(1, round(layout.column_pitch * 0.427))
            height = max(1, round(layout.row_pitch * 0.146))
            if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
                continue
            value = card.crop((x - card_box[0], y - card_box[1], x - card_box[0] + width, y - card_box[1] + height))
            red, green, blue = value.split()
            minimum = ImageChops.darker(ImageChops.darker(red, green), blue)
            maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
            brightness = minimum.point(lambda pixel: 255 if pixel >= 140 else 0)
            neutral = ImageChops.subtract(maximum, minimum).point(lambda pixel: 255 if pixel <= 60 else 0)
            mask = ImageChops.multiply(brightness, neutral)
            bounds = mask.getbbox()
            if bounds is None:
                continue
            # Trim horizontally only: preserve the baseline of the original digits.
            value = ImageOps.invert(mask.crop((bounds[0], 0, bounds[2], height)))
            value = value.resize((max(1, round(value.width * 46 / height)), 46), Image.Resampling.NEAREST)
            label_right = draw.textbbox((label_x, label_y), label, font=font)[2]
            sheet.paste(value, (label_right + 3, index * 90 + 16))
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as output:
        output_path = Path(output.name)
    try:
        sheet.save(output_path, format="PNG")
    except Exception:
        output_path.unlink(missing_ok=True)
        raise
    return output_path


def _ambiguous_fields(result: OcrParseResult) -> set[tuple[str, int]]:
    return {(field, index) for field in ("level", "speed", "count")
            for index, label in enumerate(RESOURCE_LABELS)
            if f"Ambiguous {field} for {label}." in result.warnings}


def _merge_ocr_results(base: OcrParseResult, alternate: OcrParseResult) -> OcrParseResult:
    """Combine independent reads, leaving conflicting values for manual review."""
    values = {"level": list(base.levels), "speed": list(base.speeds), "count": list(base.counts)}
    other = {"level": alternate.levels, "speed": alternate.speeds, "count": alternate.counts}
    ambiguous = _ambiguous_fields(base)
    alternate_ambiguous = _ambiguous_fields(alternate)
    labels = {"level": "Level", "speed": "Speed", "count": "Count"}
    warnings = [warning for warning in base.warnings
                if not warning.startswith(("Missing ", "Ambiguous "))]
    for field, items in values.items():
        for index, value in enumerate(items):
            key = field, index
            candidate = other[field][index]
            if key not in ambiguous:
                if value is None:
                    if key in alternate_ambiguous:
                        ambiguous.add(key)
                    else:
                        items[index] = candidate
                elif candidate is not None and value != candidate:
                    ambiguous.add(key)
            if key in ambiguous:
                items[index] = None
                warnings.append(f"Ambiguous {field} for {RESOURCE_LABELS[index]}.")
            if items[index] is None:
                warnings.append(f"Missing {labels[field]} for {RESOURCE_LABELS[index]}.")
    duration = base.duration_seconds if base.duration_seconds is not None else alternate.duration_seconds
    if (base.duration_seconds is not None and alternate.duration_seconds is not None
            and base.duration_seconds != alternate.duration_seconds):
        duration = None
    return OcrParseResult(
        levels=values["level"], speeds=values["speed"], counts=values["count"],
        duration_seconds=duration, warnings=list(dict.fromkeys(warnings)),
        complete=all(value is not None for items in values.values() for value in items),
    )


def _parse_event_duration(lines: list[OcrLine]) -> int | None:
    """Read only the event's labelled timer; absent or ambiguous headers are optional."""
    headers = [line.text for line in lines if re.match(r"\s*Event\s+time\s+left\b", line.text, re.IGNORECASE)]
    if len(headers) != 1:
        return None
    match = re.fullmatch(
        r"\s*Event\s+time\s+left\s*:\s*(?:(\d+)\s+days?\s*,?\s*)?"
        r"(\d(?:\s*\d)?)\s*:\s*(\d\s*\d)\s*:\s*(\d\s*\d)\s+hours?\s*",
        headers[0], re.IGNORECASE,
    )
    if match is None:
        return None
    days, hours, minutes, seconds = (int(re.sub(r"\s+", "", value) if value else 0) for value in match.groups())
    if hours > 23 or minutes > 59 or seconds > 59:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


async def _recover_event_duration(
    image_path: Path, lines: list[OcrLine], image_width: int, engine: OcrEngineId,
) -> int | None:
    """Read the labelled timer separately when the full image loses clock digits."""
    from PIL import Image, ImageOps

    headers = [line for line in lines if re.match(r"\s*Event\s+time\s+left\b", line.text, re.IGNORECASE)]
    if len(headers) != 1 or image_width <= 0:
        return None
    line = headers[0]
    with Image.open(image_path) as source:
        image = source.convert("L")
    factor = image.width / image_width
    top = max(0, round((line.top - line.height * 0.5) * factor))
    bottom = min(image.height, round((line.top + line.height * 1.5) * factor))
    if bottom <= top:
        return None
    crop = image.crop((0, top, image.width, bottom))
    crop = crop.resize((max(1, round(crop.width * 92 / crop.height)), 92), Image.Resampling.LANCZOS)
    for threshold in (None, 120, 175):
        prepared = crop if threshold is None else crop.point(lambda pixel: 255 if pixel >= threshold else 0)
        prepared = ImageOps.expand(ImageOps.invert(prepared), border=20, fill="white")
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as output:
            prepared_path = Path(output.name)
        try:
            prepared.save(prepared_path, format="PNG")
            recovered_lines, _, _ = await extract_lines_ocr(prepared_path, engine)
        finally:
            prepared_path.unlink(missing_ok=True)
        duration = _parse_event_duration(recovered_lines)
        if duration is not None:
            return duration
    return None


async def _recover_missing_fields(
    image_path: Path, result: OcrParseResult, layout: GridLayout, engine: OcrEngineId,
) -> OcrParseResult:
    values = {"level": list(result.levels), "speed": list(result.speeds), "count": list(result.counts)}
    ambiguous = _ambiguous_fields(result)
    warnings = list(result.warnings)
    prepared_path = _prepare_field_sheet(image_path, layout)
    try:
        lines, sheet_width, _ = await extract_lines_ocr(prepared_path, engine)
    finally:
        prepared_path.unlink(missing_ok=True)
    candidates: dict[tuple[str, int], set[int]] = {}
    for line in _merge_orphan_numbers(lines, sheet_width):
        parsed = _parse_line(line)
        if parsed is None:
            continue
        field, value = parsed
        index = int(line.center_y // 90)
        column = int(line.center_x // 600)
        if 0 <= index < 12 and column == ("level", "speed", "count").index(field):
            candidates.setdefault((field, index), set()).add(value)
    for (field, index), numbers in candidates.items():
        if (field, index) in ambiguous or len(numbers) != 1:
            continue
        candidate = next(iter(numbers))
        if values[field][index] is None:
            values[field][index] = candidate
        elif field != "count" and values[field][index] != candidate:
            # Background strokes can look like an extra digit (e.g. 10 -> 107).
            values[field][index] = None
            warnings.append(f"Ambiguous {field} for {RESOURCE_LABELS[index]}.")
            warnings.append(f"Missing {field.title()} for {RESOURCE_LABELS[index]}.")
    labels = {"level": "Level", "speed": "Speed", "count": "Count"}
    warnings = [warning for warning in dict.fromkeys(warnings) if not any(
        values[field][index] is not None and warning == f"Missing {labels[field]} for {RESOURCE_LABELS[index]}."
        for field in values for index in range(12))]
    return OcrParseResult(
        levels=values["level"], speeds=values["speed"], counts=values["count"], warnings=warnings,
        duration_seconds=result.duration_seconds,
        complete=all(value is not None for items in values.values() for value in items),
    )


async def parse_image(image_path: Path, engine: OcrEngineId = "tesseract") -> OcrParseResult:
    # Natural RGB preserves fine digit strokes that a global threshold can erase.
    prepared_path = _prepare_ocr_image(image_path, scale=None, threshold=None)
    try:
        lines, image_width, image_height = await extract_lines_ocr(prepared_path, engine)
    finally:
        prepared_path.unlink(missing_ok=True)
    result = parse_ocr_lines(lines, image_width, image_height)
    result.duration_seconds = _parse_event_duration(lines)
    layout = _detect_grid_layout(lines)
    if layout is None:
        if result.duration_seconds is None:
            result.duration_seconds = await _recover_event_duration(image_path, lines, image_width, engine)
        return result
    from PIL import Image
    with Image.open(image_path) as image:
        factor = image.width / image_width
        alternate_scale = max(2, round(4000 / image.width))
    # Fine leading digits and separators can disappear at a single image scale.
    prepared_path = _prepare_ocr_image(image_path, scale=alternate_scale, threshold=None)
    try:
        alternate_lines, alternate_width, alternate_height = await extract_lines_ocr(prepared_path, engine)
    finally:
        prepared_path.unlink(missing_ok=True)
    alternate = parse_ocr_lines(alternate_lines, alternate_width, alternate_height)
    alternate.duration_seconds = _parse_event_duration(alternate_lines)
    result = _merge_ocr_results(result, alternate)
    layout = GridLayout(*(value * factor for value in (
        layout.left_title_center, layout.top_title_center, layout.column_pitch, layout.row_pitch)))
    if not result.complete:
        result = await _recover_missing_fields(image_path, result, layout, engine)
    if result.duration_seconds is None:
        result.duration_seconds = await _recover_event_duration(image_path, lines, image_width, engine)
    return result
