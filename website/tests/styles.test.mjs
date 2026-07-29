import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const cssUrl = new URL("../app/globals.css", import.meta.url);

test("keeps search result and empty-state rules as separate valid blocks", async () => {
  const css = await readFile(cssUrl, "utf8");

  assert.match(
    css,
    /\.search-results span\s*\{\s*color:\s*var\(--muted\);\s*font-size:\s*12px;\s*\}/s,
  );
  assert.match(
    css,
    /\.search-empty\s*\{\s*margin:\s*0;\s*padding:\s*14px;\s*color:\s*var\(--muted\);/s,
  );
  assert.doesNotMatch(css, /\.search-results span\s*\{\s*\.search-empty/s);
});
