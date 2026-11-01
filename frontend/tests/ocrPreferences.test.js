import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { readOcrEngine, writeOcrEngine } from "../src/ocrPreferences.js";
import { WORKSPACE_KEY } from "../src/workspace.js";
import { gameInput, memoryStorage } from "./fixtures.js";

const windowsEngines = [
  { id: "tesseract", name: "Tesseract" },
  { id: "windows", name: "Windows OCR" },
];

describe("Choosing the screenshot reader", () => {
  test("defaults to Tesseract and remembers Windows OCR separately from the game draft", () => {
    const draft = JSON.stringify({ input: gameInput() });
    const storage = memoryStorage({ [WORKSPACE_KEY]: draft });
    assert.equal(readOcrEngine(storage, windowsEngines), "tesseract");

    writeOcrEngine(storage, "windows");

    assert.equal(readOcrEngine(storage, windowsEngines), "windows");
    assert.equal(storage.getItem(WORKSPACE_KEY), draft);
  });

  test("uses Tesseract when a Linux server cannot honor a saved Windows OCR preference", () => {
    const storage = memoryStorage();
    writeOcrEngine(storage, "windows");
    const linuxEngines = windowsEngines.filter(engine => engine.id === "tesseract");

    assert.equal(readOcrEngine(storage, linuxEngines), "tesseract");
    assert.equal(readOcrEngine(storage, windowsEngines), "windows");
  });

  test("falls back to Tesseract when browser preferences cannot be saved", () => {
    const unavailable = {
      getItem: () => {
        throw new Error("Storage unavailable");
      },
      setItem: () => {
        throw new Error("Storage unavailable");
      },
    };

    assert.equal(readOcrEngine(unavailable, windowsEngines), "tesseract");
    assert.equal(writeOcrEngine(unavailable, "windows"), false);
  });
});
