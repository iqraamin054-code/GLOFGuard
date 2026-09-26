import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { fileURLToPath } from "node:url";

const clientPath = fileURLToPath(
  new URL("../src/lib/supabase.ts", import.meta.url),
);

test("browser Supabase client accepts only a publishable key variable", () => {
  const source = readFileSync(clientPath, "utf8");

  assert.match(source, /NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY/);
  assert.doesNotMatch(source, /NEXT_PUBLIC_SUPABASE_ANON_KEY/);
  assert.doesNotMatch(source, /SUPABASE_SECRET_KEY/);
});
