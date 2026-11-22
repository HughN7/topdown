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

test("test-runner branching flags LLM-07 in prod files and skips test files", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const prod = [
    "export function sendEmail(address: string): string {",
    '  if (process.env.NODE_ENV === "test" || process.env.VITEST) {',
    '    return "stub";',
    "  }",
    "  return address;",
    "}",
  ].join("\n");
  assert.ok(checkSource(prod, "src/mailer.ts").some((entry) => entry.rule === "LLM-07"));
  assert.deepEqual(checkSource(prod, "src/__tests__/mailer.test.ts"), []);
});

test("CLI supports --backend=heuristic, --show-backend, and refuses --fix without AST", () => {
  const cleanFixture = path.join(rootDir, "examples", "fixtures", "ts_after.ts");
  const heurProc = spawnSync(
    process.execPath,
    [checkerPath, "--backend=heuristic", "--show-backend", cleanFixture],
    { encoding: "utf8" },
  );
  assert.equal(heurProc.status, 0, heurProc.stderr);
  assert.match(heurProc.stderr, /backend:\s*heuristic/);

  const fixHeurProc = spawnSync(
    process.execPath,
    [checkerPath, "--backend=heuristic", "--fix", cleanFixture],
    { encoding: "utf8" },
  );
  assert.equal(fixHeurProc.status, 2);
  assert.match(fixHeurProc.stderr, /AST|typescript/i);
});

test("optional AST backend exports checkSourceWithAst and fixSourceWithAst", async () => {
  const mod = await import("../enforcement/check_ts.mjs");
  assert.equal(typeof mod.checkSourceWithAst, "function");
  assert.equal(typeof mod.fixSourceWithAst, "function");
  assert.equal(typeof mod.loadTypeScript, "function");

  const tsMod = await mod.loadTypeScript();
  if (!tsMod) {
    const cleanFixture = path.join(rootDir, "examples", "fixtures", "ts_after.ts");
    const astProc = spawnSync(process.execPath, [checkerPath, "--backend=ast", cleanFixture], {
      encoding: "utf8",
    });
    assert.equal(astProc.status, 2);
    assert.match(astProc.stderr, /typescript/i);
    return;
  }

  const outOfOrder = [
    "// L3 Leaf",
    "function formatUsd(amountCents: number): string {",
    "  return `$${(amountCents / 100).toFixed(2)}`;",
    "}",
    "",
    "export function renderTotal(amountCents: number): string {",
    "  return formatUsd(amountCents);",
    "}",
  ].join("\n");
  assert.ok(mod.checkSourceWithAst(outOfOrder, "sample.ts", tsMod).some((e) => e.rule === "CANON-01"));
  const fixed = mod.fixSourceWithAst(outOfOrder, "sample.ts", tsMod);
  assert.equal(fixed.refusal, null);
  assert.ok(fixed.source.indexOf("function renderTotal") < fixed.source.indexOf("function formatUsd"));
  assert.deepEqual(mod.checkSourceWithAst(fixed.source, "sample.ts", tsMod), []);
});

test("positive-witness TS idioms pass with 0 violations and 0 waivers", async () => {
  const fs = await import("node:fs");
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const witnessPath = path.join(rootDir, "tests", "positive_witness", "ts_idioms.ts");
  const source = fs.readFileSync(witnessPath, "utf8");
  assert.ok(!source.includes("topdown-ignore"));
  assert.deepEqual(checkSource(source, witnessPath), []);
});

test("JSX apostrophes, destructured signatures, and quoted optional keys handled cleanly", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const jsxWithViolation = [
    "export function Card(flagA: boolean, flagB: boolean, flagC: boolean) {",
    "  const view = <div>it's fine</div>;",
    "  if (flagA) {",
    "    if (flagB) {",
    "      if (flagC) {",
    "        return view;",
    "      }",
    "    }",
    "  }",
    "  return null;",
    "}",
  ].join("\n");
  const destructuredDeep = [
    "export function run(",
    "  { flagA, flagB, flagC }: { flagA: boolean; flagB: boolean; flagC: boolean },",
    "): number {",
    "  if (flagA) {",
    "    if (flagB) {",
    "      if (flagC) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const quotedOptionalKeys = 'type Headers = { "x-a"?: string; "x-b"?: string };\n';
  const siblingTernaries = [
    "export function render(x: boolean, y: boolean) {",
    "  const tpl = `${x ? 1 : 2}-${y ? 3 : 4}`;",
    "  const jsx = <div>{x ? 1 : 2}{y ? 3 : 4}</div>;",
    "  const eq = (x ? 1 : 2) === (y ? 3 : 4);",
    "  const curried = ((a: number) => (b: number) => a + b)(x ? 1 : 2)(y ? 3 : 4);",
    "  return { tpl, jsx, eq, curried };",
    "}",
  ].join("\n");
  assert.ok(checkSource(jsxWithViolation, "Card.tsx").some((e) => e.rule === "CANON-05"));
  assert.ok(checkSource(destructuredDeep, "run.ts").some((e) => e.rule === "CANON-05"));
  assert.deepEqual(checkSource(quotedOptionalKeys, "types.ts"), []);
  assert.deepEqual(checkSource(siblingTernaries, "siblings.tsx"), []);
});
