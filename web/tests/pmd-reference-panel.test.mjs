import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { test } from "node:test";
import { runInNewContext } from "node:vm";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

// Render the real TSX component in memory using the existing compiler: no new
// test dependencies, generated files, database access, or environment loading.
const source = readFileSync(
  new URL("../src/app/components/pmd-reference-panel.tsx", import.meta.url),
  "utf8",
);
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS },
});
const componentModule = { exports: {} };
runInNewContext(outputText, {
  module: componentModule,
  exports: componentModule.exports,
  require: createRequire(import.meta.url),
});
const markup = renderToStaticMarkup(createElement(componentModule.exports.default));

test("renders the exact historical source name, badge, and disclaimer", () => {
  assert.ok(markup.includes("Pakistan Meteorological Department (PMD) — Glacial Lakes Inventory 2013"));
  assert.ok(markup.includes("PMD Official Inventory · 2013"));
  assert.ok(markup.includes("Historical reference inventory — not a live warning feed."));
  assert.ok(markup.includes('aria-labelledby="pmd-reference-heading"'));
  assert.ok(markup.includes('id="pmd-reference-heading"'));
});

test("keeps historical source review separate from current observations and lake IDs", () => {
  assert.ok(markup.includes("does not replace the current GLOF Guard"));
  assert.ok(markup.includes("Sentinel-2, GSMaP, GFS, and NASA POWER measurements"));
  assert.ok(markup.includes("PMD lake IDs are not PKGL IDs"));
  assert.ok(markup.includes("Local reference · review pending"));
  assert.ok(markup.includes("Not imported to Supabase or applied to the monitoring map"));
});
