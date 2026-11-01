import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { describe, test } from "node:test";

import { readLanguage, translate, writeLanguage } from "../src/i18n.js";
import russian from "../src/locales/ru.js";
import { WORKSPACE_KEY } from "../src/workspace.js";
import { gameInput, memoryStorage } from "./fixtures.js";

function translationKeysIn(node, keys = new Set()) {
  if (!node || typeof node !== "object") return keys;
  if (node.type === "CallExpression"
      && node.callee?.name === "t"
      && node.arguments[0]?.type === "StringLiteral") {
    keys.add(node.arguments[0].value);
  }
  for (const child of Object.values(node)) {
    if (Array.isArray(child)) {
      child.forEach(item => translationKeysIn(item, keys));
    } else if (child && typeof child === "object") {
      translationKeysIn(child, keys);
    }
  }
  return keys;
}

describe("Switching the interface language", () => {
  test("defaults to English and remembers Russian without changing the working draft", () => {
    const draft = JSON.stringify({ input: gameInput(), selectedRunId: 17 });
    const storage = memoryStorage({ [WORKSPACE_KEY]: draft });
    assert.equal(readLanguage(storage), "en");

    writeLanguage(storage, "ru");

    assert.equal(readLanguage(storage), "ru");
    assert.equal(translate("Planner", readLanguage(storage)), "Планировщик");
    assert.equal(storage.getItem(WORKSPACE_KEY), draft);
  });

  test("keeps the interface usable when browser storage is unavailable", () => {
    const unavailable = {
      getItem: () => {
        throw new Error("Storage unavailable");
      },
      setItem: () => {
        throw new Error("Storage unavailable");
      },
    };

    assert.equal(readLanguage(unavailable), "en");
    assert.equal(writeLanguage(unavailable, "ru"), false);
  });

  test("translates saved notices in both directions while preserving literal profile names", () => {
    const profileName = 'My "Rare" Профиль $& {0}';
    const template = 'Profile "{0}" saved, including search limits.';
    const english = `Profile "${profileName}" saved, including search limits.`;
    const localized = `Профиль «${profileName}» сохранён вместе с лимитами поиска.`;

    assert.equal(translate(template, "en", [profileName]), english);
    assert.equal(translate(english, "ru"), localized);
    assert.equal(translate(localized, "en"), english);
  });

  test("Russian covers the interface messages with the same interpolation fields", async () => {
    const { parse } = await import("@babel/parser");
    const source = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
    const tree = parse(source, { sourceType: "module", plugins: ["jsx"] });
    const keys = translationKeysIn(tree);
    const missing = [...keys].filter(key => !Object.hasOwn(russian, key));
    const fields = text => (
      [...text.matchAll(/\{(\d+)\}/g)].map(match => match[1]).sort()
    );

    assert.ok(keys.size > 0, "the catalog check must cover interface messages");
    assert.deepEqual(missing, [], "UI messages without Russian translations");
    for (const [english, localized] of Object.entries(russian)) {
      assert.deepEqual(fields(localized), fields(english), english);
    }
  });
});
