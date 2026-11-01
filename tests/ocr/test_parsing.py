"""Map OCR lines to the correct resource cells, keeping ambiguity visible."""

import pytest

from itrtg_web.ocr import OcrLine, parse_ocr_lines


@pytest.mark.parametrize("offset,scale,headings", [
    pytest.param((0, 0), 1, True, id="game-grid"),
    pytest.param((350, 120), 1.5, True, id="resized-grid-with-margins"),
    pytest.param((0, 0), 1, False, id="resource-headings-unreadable"),
])
def test_each_resource_keeps_its_level_speed_and_count(
    recognized_grid, resource_values, offset, scale, headings,
):
    reading = recognized_grid(
        offset=offset, scale=scale, headings=headings,
        overrides={("Cup", "count"): "Count: 5.125 E+6", ("Free Exp", "count"): "Count: O"},
    )

    result = parse_ocr_lines(*reading)

    assert result.complete
    assert result.warnings == []
    assert result.levels == resource_values["levels"]
    assert result.speeds == resource_values["speeds"]
    assert result.counts == resource_values["counts"]


def test_unreadable_numbers_stay_in_their_own_cells(recognized_grid, resource_values):
    reading = recognized_grid(overrides={
        ("Candle", "speed"): None,
        ("Broom", "count"): "Count: 1,5",
    })

    result = parse_ocr_lines(*reading)

    assert not result.complete
    assert result.speeds[4] is None
    assert result.counts[5] is None
    assert result.speeds[5] == resource_values["speeds"][5]
    assert result.counts[4] == resource_values["counts"][4]


def test_conflicting_values_in_one_card_require_review(recognized_grid):
    lines, width, height = recognized_grid()
    lines.append(OcrLine("Level: 99", 120, 180, 180, 22))

    result = parse_ocr_lines(lines, width, height)

    assert result.levels[0] is None
    assert result.levels[1] == 10
    assert "Ambiguous level for Grave Stone." in result.warnings


@pytest.mark.parametrize("number_left,expected", [
    pytest.param(195, 1577, id="adjacent-number"),
    pytest.param(90, None, id="unrelated-number-to-the-left"),
    pytest.param(280, None, id="distant-number-in-the-same-column"),
])
def test_split_numbers_are_joined_only_to_their_own_label(recognized_grid, number_left, expected):
    lines, width, height = recognized_grid(overrides={("Research Points", "count"): "Count:"})
    target = next(
        index for index, line in enumerate(lines) if line.text == "Count:" and line.top > 600
    )
    label = lines[target]
    lines[target] = OcrLine(label.text, label.left, label.top, 65, label.height)
    lines.append(OcrLine("I , 577", number_left, label.top, 65, label.height))

    result = parse_ocr_lines(lines, width, height)

    assert result.counts[9] == expected
    assert result.counts[10] == 0
