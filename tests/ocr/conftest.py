from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw
import pytest

from itrtg_web import ocr, tesseract_ocr
from itrtg_web.models import RESOURCE_LABELS
from itrtg_web.ocr import OcrLine


@pytest.fixture
def resource_values():
    return {
        "levels": [2, 10, 3, 2, 2, 1, 0, 1, 0, 0, 0, 0],
        "speeds": [2, 9, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0],
        "counts": [0, 1577, 5125000, 2000, 3000, 4000, 0, 5000, 0, 0, 0, 0],
    }


@pytest.fixture
def recognized_grid(resource_values):
    """Build OCR readings for a complete game grid; None marks an unread field."""
    def build(*, overrides=None, offset=(0, 0), scale=1, timer=None, headings=True):
        lines = []
        for index, name in enumerate(RESOURCE_LABELS):
            row, column = divmod(index, 3)
            left, top = 120 + column * 300, 145 + row * 150
            if headings:
                lines.append(OcrLine(name, left, top, 200, 24))
            for field_index, (field, attribute) in enumerate([
                ("level", "levels"), ("speed", "speeds"), ("count", "counts"),
            ]):
                value = resource_values[attribute][index]
                text = f"{field.title()}: {value:,}"
                text = (overrides or {}).get((name, field), text)
                if text is not None:
                    lines.append(OcrLine(text, left, top + 35 + field_index * 30, 180, 22))
        if timer is not None:
            lines.append(OcrLine(timer, 10, 10, 700, 24))
        shifted = [OcrLine(
            line.text, (line.left + offset[0]) * scale, (line.top + offset[1]) * scale,
            line.width * scale, line.height * scale,
        ) for line in lines]
        return shifted, round((1000 + offset[0]) * scale), round((800 + offset[1]) * scale)

    return build


@pytest.fixture
def screenshot(tmp_path):
    path = tmp_path / "game state.png"
    Image.new("RGB", (1000, 800), "white").save(path)
    return path


@pytest.fixture
def colored_screenshot(tmp_path):
    path = tmp_path / "game state.png"
    image = Image.new("RGB", (300, 150), (70, 0, 150))
    ImageDraw.Draw(image).text((20, 20), "Level: 10", fill="white")
    image.save(path)
    return path


@pytest.fixture
def ocr_reader(monkeypatch, tmp_path):
    """Replace the OS OCR boundary, keeping real image preparation and parsing."""
    reader = SimpleNamespace(readings=[], paths=[], engines=[])
    last_reading = None

    async def recognize(path, engine="tesseract"):
        nonlocal last_reading
        reader.paths.append(path)
        reader.engines.append(engine)
        if reader.readings:
            last_reading = reader.readings.pop(0)
        if isinstance(last_reading, Exception):
            raise last_reading
        if last_reading is None:
            pytest.fail("Configure an OCR reading before importing a screenshot.")
        lines, width, height = last_reading
        with Image.open(path) as image:
            actual_width, actual_height = image.size
        return [OcrLine(
            line.text, line.left * actual_width / width, line.top * actual_height / height,
            line.width * actual_width / width, line.height * actual_height / height,
        ) for line in lines], actual_width, actual_height

    monkeypatch.setattr(ocr.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(ocr, "extract_lines_ocr", recognize)
    return reader


@pytest.fixture
def tesseract_tsv():
    columns = (
        "level", "page_num", "block_num", "par_num", "line_num", "word_num",
        "left", "top", "width", "height", "conf", "text",
    )

    def build(*words):
        defaults = dict(zip(columns, [5, 1, 1, 1, 1, 1, 100, 100, 60, 24, 95, ""]))
        rows = [{**defaults, **word} for word in words]
        return "\n".join([
            "\t".join(columns),
            *("\t".join(str(row[column]) for column in columns) for row in rows),
        ])

    return build


@pytest.fixture
def tesseract_process(monkeypatch, tmp_path, tesseract_tsv):
    process = SimpleNamespace(
        output=tesseract_tsv({"text": "Count: 0"}), error=None, returncode=0,
        paths=[], image_sizes=[], options=[],
    )

    def run(arguments, **kwargs):
        path = Path(arguments[1])
        process.paths.append(path)
        process.options.append(kwargs)
        with Image.open(path) as image:
            process.image_sizes.append(image.size)
        if process.error is not None:
            raise process.error
        return SimpleNamespace(
            returncode=process.returncode,
            stdout=process.output.encode(),
            stderr=b"private diagnostic",
        )

    monkeypatch.setattr(tesseract_ocr.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(tesseract_ocr, "resolve_tesseract_binary", lambda: "test-tesseract")
    monkeypatch.setattr(tesseract_ocr.subprocess, "run", run)
    return process
