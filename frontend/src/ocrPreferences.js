export const OCR_ENGINE_KEY = "itrtg:ocr-engine";
export const DEFAULT_OCR_ENGINE = "tesseract";

export function readOcrEngine(storage, availableEngines) {
  try {
    const selected = storage.getItem(OCR_ENGINE_KEY);
    return availableEngines.some(engine => engine.id === selected)
      ? selected
      : DEFAULT_OCR_ENGINE;
  } catch {
    return DEFAULT_OCR_ENGINE;
  }
}

export function writeOcrEngine(storage, engine) {
  if (engine !== "tesseract" && engine !== "windows") return false;
  try {
    storage.setItem(OCR_ENGINE_KEY, engine);
    return true;
  } catch {
    return false;
  }
}
