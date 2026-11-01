import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { durationFromParts, durationParts, formatScore } from "../src/runPresentation.js";

describe("Presenting game time and optimization points", () => {
  test("editing an imported event period preserves its exact seconds", () => {
    const imported = 13 * 86400 + 23 * 3600 + 42;
    const fields = durationParts(imported);

    assert.deepEqual(fields, { days: 13, hours: 23, minutes: 0, seconds: 42 });
    assert.equal(durationFromParts(fields), imported);
    assert.equal(durationFromParts({ ...fields, minutes: 5 }), imported + 5 * 60);
  });

  test("an ended event stays at zero when converted to editable fields", () => {
    assert.equal(durationFromParts(durationParts(0)), 0);
  });

  test("shows concise localized points without confusing zero, unknown and tiny scores", () => {
    const examples = [
      { name: "three decimal places", value: 27.887601, locale: "en-US", expected: "27.888" },
      { name: "no padded zeros", value: 27, locale: "en-US", expected: "27" },
      {
        name: "Russian decimal separator",
        value: 27.887601,
        locale: "ru-RU",
        expected: "27,888",
      },
      { name: "a true zero", value: 0, locale: "en-US", expected: "0" },
      { name: "no score yet", value: null, locale: "en-US", expected: "—" },
      {
        name: "a small nonzero score",
        value: 1.23456789e-12,
        locale: "en-US",
        expected: "1.235E-12",
      },
    ];

    for (const example of examples) {
      assert.equal(formatScore(example.value, example.locale), example.expected, example.name);
    }
  });
});
