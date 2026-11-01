"""Import a screenshot without inventing unreadable resources or event time."""

import asyncio
from types import SimpleNamespace

import pytest

from itrtg_web import ocr
from itrtg_web.ocr import OcrLine


@pytest.mark.parametrize("timer,expected", [
    pytest.param("Event time left: 13 days, 2 1 : 1 2 : 1 5 hours", 1199535, id="spaced-digits"),
    pytest.param("Event time left: 00:00:00 hours", 0, id="ended-event"),
    pytest.param(None, None, id="cropped-header"),
    pytest.param("Event time left: 13 days, 21:60:15 hours", None, id="unreadable-clock"),
])
def test_event_time_is_optional_and_preserves_exact_seconds(
    screenshot, recognized_grid, ocr_reader, timer, expected,
):
    ocr_reader.readings.append(recognized_grid(timer=timer))

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.complete
    assert result.duration_seconds == expected


def test_two_event_timers_do_not_choose_a_time_for_the_user(
    screenshot, recognized_grid, ocr_reader,
):
    lines, width, height = recognized_grid(timer="Event time left: 13 days, 21:12:15 hours")
    lines.append(OcrLine("Event time left: 12 days, 21:12:15 hours", 10, 40, 700, 24))
    ocr_reader.readings.append((lines, width, height))

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.complete
    assert result.duration_seconds is None


def test_timer_can_be_recovered_without_changing_the_resource_values(
    screenshot, recognized_grid, resource_values, ocr_reader,
):
    truncated = recognized_grid(timer="Event time left: 13 days, 21")
    recovered = (
        [OcrLine("Event time left: 13 days, 21 : 1 2:15 hours", 10, 10, 700, 24)], 1000, 80,
    )
    ocr_reader.readings.extend([truncated, truncated, recovered])

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.duration_seconds == 1199535
    assert result.counts == resource_values["counts"]
    assert result.complete


def test_another_read_recovers_missing_fields_and_keeps_real_zeroes(
    screenshot, recognized_grid, resource_values, ocr_reader,
):
    ocr_reader.readings.extend([
        recognized_grid(overrides={("Candle", "speed"): None, ("Pet Stones", "count"): None}),
        recognized_grid(),
    ])

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.complete
    assert result.warnings == []
    assert result.speeds == resource_values["speeds"]
    assert result.counts == resource_values["counts"]


def test_conflicting_reads_are_not_silently_repaired_by_another_guess(
    screenshot, recognized_grid, ocr_reader,
):
    ocr_reader.readings.extend([
        recognized_grid(overrides={("Broom", "count"): "Count: 577"}),
        recognized_grid(overrides={("Broom", "count"): "Count: 1,577"}),
        ([OcrLine("Count: 1577", 1210, 470, 200, 40)], 1800, 1080),
    ])

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.counts[5] is None
    assert result.counts[4] == 3000
    assert "Ambiguous count for Broom." in result.warnings


def test_isolated_digits_flag_an_extra_level_digit_without_truncating_known_counts(
    screenshot, recognized_grid, ocr_reader,
):
    raw = recognized_grid(overrides={
        ("Spider Web", "level"): "Level: 107", ("Witch Hat", "level"): None,
    })
    digit_sheet = [
        OcrLine("Level: 10", 10, 110, 160, 40),
        OcrLine("Level: 0", 10, 920, 160, 40),
        OcrLine("Count: 125", 1210, 200, 200, 40),
    ]
    ocr_reader.readings.extend([raw, raw, (digit_sheet, 1800, 1080)])

    result = asyncio.run(ocr.parse_image(screenshot))

    assert result.levels[1] is None
    assert result.levels[10] == 0
    assert result.counts[2] == 5125000
    assert "Ambiguous level for Spider Web." in result.warnings


@pytest.mark.parametrize("engine", [None, "windows"], ids=["default-tesseract", "explicit-windows"])
def test_upload_keeps_the_selected_reader_for_resource_and_timer_recovery(
    client, screenshot, recognized_grid, ocr_reader, monkeypatch, engine,
):
    monkeypatch.setattr(ocr, "sys", SimpleNamespace(platform="win32"))
    original = screenshot.read_bytes()
    incomplete = recognized_grid(
        overrides={("Spider Web", "level"): None}, timer="Event time left: 13 days, 21",
    )
    digit_sheet = ([OcrLine("Level: 10", 10, 110, 160, 40)], 1800, 1080)
    timer = ([OcrLine("Event time left: 13 days, 21:12:15 hours", 10, 10, 700, 24)], 1000, 80)
    ocr_reader.readings.extend([incomplete, incomplete, digit_sheet, timer])

    response = client.post(
        "/api/ocr/parse", files={"file": ("state.png", original, "image/png")},
        data={} if engine is None else {"engine": engine},
    )

    assert response.status_code == 200
    assert response.json()["complete"]
    assert response.json()["duration_seconds"] == 1199535
    assert set(ocr_reader.engines) == {engine or "tesseract"}
    assert screenshot.read_bytes() == original
    assert set(screenshot.parent.glob("*.png")) == {screenshot}


@pytest.mark.parametrize("platform,expected", [
    pytest.param("linux", [{"id": "tesseract", "name": "Tesseract"}], id="linux"),
    pytest.param("win32", [
        {"id": "tesseract", "name": "Tesseract"}, {"id": "windows", "name": "Windows OCR"},
    ], id="windows"),
])
def test_ocr_choices_offer_windows_only_on_a_windows_server(client, monkeypatch, platform, expected):
    monkeypatch.setattr(ocr, "sys", SimpleNamespace(platform=platform))

    response = client.get("/api/ocr/engines")

    assert response.status_code == 200
    assert response.json() == expected


@pytest.mark.parametrize("platform,engine,status", [
    pytest.param("linux", "windows", 400, id="windows-unavailable-on-linux"),
    pytest.param("win32", "unknown", 422, id="unknown-engine"),
])
def test_unavailable_ocr_engines_are_rejected_without_reading_the_image(
    client, screenshot, ocr_reader, monkeypatch, platform, engine, status,
):
    monkeypatch.setattr(ocr, "sys", SimpleNamespace(platform=platform))

    response = client.post(
        "/api/ocr/parse", files={"file": ("state.png", screenshot.read_bytes(), "image/png")},
        data={"engine": engine},
    )

    assert response.status_code == status
    assert ocr_reader.paths == []


def test_failed_upload_removes_temporary_copies_and_keeps_the_original(
    client, screenshot, recognized_grid, ocr_reader,
):
    original = screenshot.read_bytes()
    ocr_reader.readings.extend([recognized_grid(), RuntimeError("OCR unavailable")])

    response = client.post("/api/ocr/parse", files={"file": ("state.png", original, "image/png")})

    assert response.status_code == 501
    assert response.json()["detail"] == "OCR unavailable"
    assert screenshot.read_bytes() == original
    assert all(not path.exists() for path in ocr_reader.paths)
    assert set(screenshot.parent.glob("*.png")) == {screenshot}


def test_invalid_uploads_are_rejected_before_ocr(client, ocr_reader):
    response = client.post(
        "/api/ocr/parse", files={"file": ("state.png", b"not an image", "image/png")},
    )

    assert response.status_code == 400
    assert ocr_reader.paths == []
