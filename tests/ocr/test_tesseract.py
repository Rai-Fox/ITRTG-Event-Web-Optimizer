"""Adapt platform OCR output and clean up temporary files on success or failure."""

import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from itrtg_web import ocr, tesseract_ocr
from itrtg_web.ocr import OcrLine


def test_tesseract_words_keep_columns_and_rows_separate(tesseract_tsv):
    document = tesseract_tsv(
        {"text": "Level:", "left": 100},
        {"text": "0", "left": 170, "width": 10, "conf": 0},
        {"text": "Level:", "left": 400},
        {"text": "10", "left": 470, "width": 25},
        {"text": "Count: 0", "left": 100, "top": 150, "block_num": 2},
        {"text": "unrecognized", "left": 100, "top": 180, "conf": -1},
    )

    lines = tesseract_ocr.parse_tesseract_tsv(document)

    assert lines == [
        OcrLine("Level: 0", 100, 100, 80, 24),
        OcrLine("Level: 10", 400, 100, 95, 24),
        OcrLine("Count: 0", 100, 150, 60, 24),
    ]


def test_an_invalid_tesseract_document_is_not_treated_as_game_data():
    with pytest.raises(RuntimeError, match="invalid OCR result"):
        tesseract_ocr.parse_tesseract_tsv("unexpected\tcolumns\n0\t10")


@pytest.mark.parametrize("platform,engine,expected_reader", [
    pytest.param("win32", None, "tesseract", id="windows-default-tesseract"),
    pytest.param("linux", None, "tesseract", id="linux-default-tesseract"),
    pytest.param("win32", "windows", "windows", id="explicit-windows-reader"),
])
def test_tesseract_is_the_default_and_windows_ocr_requires_an_explicit_choice(
    monkeypatch, screenshot, platform, engine, expected_reader,
):
    async def windows_reader(_path):
        return "windows"

    async def linux_reader(_path):
        return "tesseract"

    monkeypatch.setattr(ocr, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(ocr, "extract_lines_windows_ocr", windows_reader)
    monkeypatch.setattr(tesseract_ocr, "extract_lines_tesseract", linux_reader)

    result = asyncio.run(ocr.extract_lines_ocr(screenshot, engine)) if engine else asyncio.run(
        ocr.extract_lines_ocr(screenshot)
    )

    assert result == expected_reader


def test_recognized_coordinates_use_the_prepared_image_dimensions(
    colored_screenshot, tesseract_process,
):
    original = colored_screenshot.read_bytes()

    lines, width, height = asyncio.run(tesseract_ocr.extract_lines_tesseract(colored_screenshot))

    assert lines == [OcrLine("Count: 0", 100, 100, 60, 24)]
    assert (width, height) == tesseract_process.image_sizes[0]
    assert colored_screenshot.read_bytes() == original
    assert all(not path.exists() for path in tesseract_process.paths)
    expected_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    assert tesseract_process.options[0]["creationflags"] == expected_flags


def test_missing_tesseract_explains_how_to_enable_ocr(monkeypatch, screenshot):
    monkeypatch.setattr(tesseract_ocr, "resolve_tesseract_binary", lambda: None)

    with pytest.raises(RuntimeError, match="Tesseract.*English language data.*Docker image"):
        asyncio.run(tesseract_ocr.extract_lines_tesseract(screenshot))


@pytest.mark.parametrize("source", ["environment", "path", "program-files", "local-appdata"])
def test_tesseract_is_found_without_requiring_a_global_path_change(monkeypatch, tmp_path, source):
    for variable in ("ITRTG_TESSERACT_PATH", "ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(tesseract_ocr, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(tesseract_ocr.shutil, "which", lambda _name: None)
    binary = tmp_path / "tesseract.exe"
    if source == "environment":
        monkeypatch.setenv("ITRTG_TESSERACT_PATH", str(binary))
        monkeypatch.setattr(tesseract_ocr.shutil, "which", lambda _name: "another-tesseract")
    elif source == "path":
        monkeypatch.setattr(tesseract_ocr.shutil, "which", lambda _name: str(binary))
    elif source == "program-files":
        monkeypatch.setenv("ProgramFiles", str(tmp_path))
        binary = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    else:
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        binary = tmp_path / "Programs" / "Tesseract-OCR" / "tesseract.exe"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.touch()

    resolved = tesseract_ocr.resolve_tesseract_binary()

    assert resolved == str(binary)


def test_an_invalid_tesseract_override_is_reported_instead_of_using_another_binary(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("ITRTG_TESSERACT_PATH", str(tmp_path / "missing.exe"))
    monkeypatch.setattr(tesseract_ocr.shutil, "which", lambda _name: "another-tesseract")

    with pytest.raises(RuntimeError, match="ITRTG_TESSERACT_PATH.*start.cmd"):
        tesseract_ocr.resolve_tesseract_binary()


@pytest.mark.parametrize("failure,message", [
    pytest.param("timeout", "timed out", id="reader-timeout"),
    pytest.param("failed-process", "OCR failed", id="reader-failed"),
])
def test_failed_recognition_reports_an_actionable_error_and_removes_temporary_images(
    colored_screenshot, tesseract_process, failure, message,
):
    if failure == "timeout":
        tesseract_process.error = subprocess.TimeoutExpired("tesseract", timeout=15)
    else:
        tesseract_process.returncode = 1

    with pytest.raises(RuntimeError, match=message) as error:
        asyncio.run(tesseract_ocr.extract_lines_tesseract(colored_screenshot))

    assert "private diagnostic" not in str(error.value)
    assert colored_screenshot.exists()
    assert all(not path.exists() for path in tesseract_process.paths)
