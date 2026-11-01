"""Real OCR regression examples supplied by the user, with values read from the game."""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from itrtg_web import ocr, tesseract_ocr


SCREENSHOTS = Path(__file__).resolve().parents[1] / "fixtures" / "screenshots"


@pytest.fixture(scope="module")
def installed_tesseract():
    binary = tesseract_ocr.resolve_tesseract_binary()
    if binary is None:
        pytest.skip("Tesseract OCR is not installed.")
    languages = subprocess.run(
        [binary, "--list-langs"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if "eng" not in languages.stdout.splitlines():
        pytest.skip("Tesseract English language data is not installed.")


@pytest.mark.parametrize("image, expected", [
    pytest.param(
        "game-initial.png",
        {
            "levels": [1, 9, 3, 1, 2, 1, 0, 1, 0, 0, 0, 0],
            "speeds": [2, 8, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0],
            "counts": [1621, 130765, 4381, 521, 1544, 656, 0, 524, 0, 0, 0, 0],
            "duration_seconds": 1206042,
        },
        id="initial-state",
    ),
    pytest.param(
        "game-progress.png",
        {
            "levels": [1, 9, 3, 1, 2, 1, 0, 1, 0, 0, 0, 0],
            "speeds": [2, 8, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0],
            "counts": [3539, 328306, 10135, 1257, 3386, 1577, 0, 1260, 0, 0, 0, 0],
            "duration_seconds": 1202359,
        },
        id="resources-after-progress",
    ),
    pytest.param(
        "game-timer-full-header.png",
        {
            "levels": [2, 10, 3, 2, 2, 1, 0, 1, 0, 0, 0, 0],
            "speeds": [2, 9, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0],
            "counts": [4514, 244138, 14461, 2094, 4770, 2269, 0, 1814, 0, 0, 0, 0],
            "duration_seconds": 1199590,
        },
        id="full-event-header",
    ),
    pytest.param(
        "game-timer-short-header.png",
        {
            "levels": [2, 10, 3, 2, 2, 1, 0, 1, 0, 0, 0, 0],
            "speeds": [2, 9, 2, 0, 1, 1, 0, 0, 0, 0, 0, 0],
            "counts": [4572, 248228, 14548, 2116, 4798, 2282, 0, 1825, 0, 0, 0, 0],
            "duration_seconds": 1199535,
        },
        id="cropped-event-header",
    ),
])
def test_game_screenshots_recover_resource_values_and_exact_event_time(
    installed_tesseract, tmp_path, monkeypatch, image, expected,
):
    monkeypatch.setattr(ocr.tempfile, "tempdir", str(tmp_path))

    result = asyncio.run(ocr.parse_image(SCREENSHOTS / image))

    assert result.complete
    assert result.levels == expected["levels"]
    assert result.speeds == expected["speeds"]
    assert result.counts == expected["counts"]
    assert result.duration_seconds == expected["duration_seconds"]
