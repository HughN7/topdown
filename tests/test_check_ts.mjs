import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(__dirname, "..");
const checkerPath = path.join(rootDir, "enforcement", "check_ts.mjs");

test("clean topdown TS module passes", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    "export function processBatch(amountCents: number): string {",
    "  if (amountCents <= 0) {",
    '    return "$0.00";',
    "  }",
    "  const feeCents = computeFee(amountCents);",
    "  return formatUsd(amountCents - feeCents);",
    "}",
    "",
    "function computeFee(amountCents: number): number {",
    "  return Math.floor(amountCents * 0.02);",
    "}",
    "",
    "function formatUsd(amountCents: number): string {",
    "  const label = amountCents > 0 ? 'Paid' : 'Zero';",
    "  return `${label}: $${(amountCents / 100).toFixed(2)}`;",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(source, "clean.ts"), []);
});

test("function over 80 lines flags CANON-01", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const body = Array.from({ length: 82 }, (_, idx) => `  total += ${idx};`).join("\n");
  const source = `export function longFn(total: number): number {\n${body}\n  return total;\n}\n`;
  const violations = checkSource(source, "long.ts");
  assert.ok(violations.some((entry) => entry.rule === "CANON-01"));
});

test("nesting depth > 2 flags CANON-05", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    "function deep(a: boolean, b: boolean, c: boolean): number {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const violations = checkSource(source, "deep.ts");
  assert.ok(violations.some((entry) => entry.rule === "CANON-05"));
});

test("nested ternary flags CANON-05 and single ternary passes", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const bad = "const label = a ? (b ? 'x' : 'y') : 'z';\n";
  const good = "const label = a ? 'x' : 'y';\n";
  assert.ok(checkSource(bad, "t1.ts").some((entry) => entry.rule === "CANON-05"));
  assert.deepEqual(checkSource(good, "t2.ts"), []);
});

test("nested function declaration inside function flags CANON-05", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    "export function outer(count: number): number {",
    "  function inner(step: number): number {",
    "    return step + 1;",
    "  }",
    "  return inner(count);",
    "}",
  ].join("\n");
  const violations = checkSource(source, "nested.ts");
  assert.ok(violations.some((entry) => entry.rule === "CANON-05"));
});

test("empty catch block flags LLM-02 and rethrowing catch passes", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const bad = "function parse(raw: string): number {\n  try {\n    return Number(raw);\n  } catch (err) {}\n  return 0;\n}\n";
  const good = "function parse(raw: string): number {\n  try {\n    return Number(raw);\n  } catch (err) {\n    throw err;\n  }\n}\n";
  assert.ok(checkSource(bad, "c1.ts").some((entry) => entry.rule === "LLM-02"));
  assert.deepEqual(checkSource(good, "c2.ts"), []);
});

test("strings, template literals, and comments do not trigger false positives", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    "// function fake() { if (a) { if (b) { if (c) {} } } } a ? b ? c : d : e",
    'const sample = "function f() { if (a) { if (b) { if (c) {} } } } a ? b ? c : d : e";',
    "const templ = `catch (e) {} a ? b ? c : d : e`;",
  ].join("\n");
  assert.deepEqual(checkSource(source, "strings.ts"), []);
});

test("topdown-ignore with reason suppresses and without reason flags CANON-08", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const suppressed = '// topdown-ignore(CANON-05, reason="legacy")\nconst label = a ? (b ? "x" : "y") : "z";\n';
  const missingReason = '// topdown-ignore(CANON-05)\nconst label = a ? (b ? "x" : "y") : "z";\n';
  assert.deepEqual(checkSource(suppressed, "ig1.ts"), []);
  assert.ok(checkSource(missingReason, "ig2.ts").some((entry) => entry.rule === "CANON-08"));
});

test("CLI missing path exits 2", () => {
  const proc = spawnSync(process.execPath, [checkerPath, path.join(rootDir, "does-not-exist.ts")], {
    encoding: "utf8",
  });
  assert.equal(proc.status, 2);
  assert.ok(proc.stderr.trim().length > 0);
});

test("regex literals with quotes do not blind subsequent nesting checks", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    'const re = /reason\\s*=\\s*(?:"([^"]+)"|\'([^\']+)\')/;',
    "function deep(a: boolean, b: boolean, c: boolean): number {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const violations = checkSource(source, "regex.ts");
  assert.ok(violations.some((entry) => entry.rule === "CANON-05"));
});

test("multi-line function signatures and arrow functions are checked", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const multilineFn = [
    "export function wrapped(",
    "  a: boolean,",
    "  b: boolean,",
    "  c: boolean,",
    "): number {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const arrowFn = [
    "export const run = (a: boolean, b: boolean, c: boolean): number => {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "};",
  ].join("\n");
  assert.ok(checkSource(multilineFn, "ml.ts").some((entry) => entry.rule === "CANON-05"));
  assert.ok(checkSource(arrowFn, "arrow.ts").some((entry) => entry.rule === "CANON-05"));
});

test("depth-2 else and catch blocks do not false-positive as depth 3", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const source = [
    "function handle(flagA: boolean, flagB: boolean): number {",
    "  if (flagA) {",
    "    if (flagB) {",
    "      return 1;",
    "    } else {",
    "      return 2;",
    "    }",
    "  }",
    "  if (flagB) {",
    "    try {",
    "      return 3;",
    "    } catch (err) {",
    "      throw err;",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(source, "else_catch.ts"), []);
});

test("bare topdown-ignore, parens in reason, and ignore inside string literal", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const bare = "// topdown-ignore\nconst x = 1;\n";
  const parensReason =
    '// topdown-ignore(CANON-05, reason="mirrors f() call")\nconst label = a ? (b ? "x" : "y") : "z";\n';
  const inString = 'const note = "// topdown-ignore(CANON-05)";\n';
  assert.ok(checkSource(bare, "b.ts").some((entry) => entry.rule === "CANON-08"));
  assert.deepEqual(checkSource(parensReason, "p.ts"), []);
  assert.deepEqual(checkSource(inString, "s.ts"), []);
});

test("multi-line empty catch flags LLM-02 and non-nested ternaries/optional tuples pass", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const multilineEmptyCatch = "function parse(raw: string): number {\n  try {\n    return Number(raw);\n  } catch (err) {\n    // ignored\n  }\n  return 0;\n}\n";
  const twoSingleTernaries = "const pair = [a ? 1 : 2, b ? 3 : 4];\ntype Pair = [number?, string?];\n";
  assert.ok(checkSource(multilineEmptyCatch, "mc.ts").some((entry) => entry.rule === "LLM-02"));
  assert.deepEqual(checkSource(twoSingleTernaries, "ok.ts"), []);
});
