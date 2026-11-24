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

test("implicit state machine lifecycle boolean soup flags CANON-03 while orthogonal booleans pass", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const badInterface = [
    "export interface QueryState {",
    "  readonly isLoading: boolean;",
    "  readonly isError: boolean;",
    "}",
  ].join("\n");
  const badSvelteStore = [
    "class CheckoutStore {",
    "  isSubmitting = $state(false);",
    "  isSuccess = $state(false);",
    "}",
  ].join("\n");
  const suppressed = [
    '// topdown-ignore(CANON-03, reason="legacy API payload")',
    "export interface LegacyPayload {",
    "  isLoading: boolean;",
    "  isError: boolean;",
    "}",
  ].join("\n");
  const cleanOrthogonal = [
    "export interface Merchant {",
    "  readonly isActive: boolean;",
    "  readonly isCleared: boolean;",
    "  readonly isLoading: boolean;",
    "}",
    "export interface OtherView {",
    "  readonly isError: boolean;",
    "}",
    "export class OrderController {",
    "  setLoading(isLoading: boolean): boolean {",
    "    return isLoading;",
    "  }",
    "  setError(isError: boolean): boolean {",
    "    return isError;",
    "  }",
    "}",
  ].join("\n");
  const bareAndHooks = [
    "export function useRequest(cond: boolean) {",
    "  const [is_loading, setLoading] = useState(false);",
    "  if (cond) {",
    "    const [hasError, setHasError] = useState(false);",
    "    return { is_loading, hasError, setLoading, setHasError };",
    "  }",
    "  return { is_loading, hasError: false, setLoading, setHasError: setLoading };",
    "}",
  ].join("\n");
  const innerIgnoreDoesNotHideOuterClass = [
    "export class OrderStore {",
    "  isLoading = false;",
    "  isError = false;",
    '  // topdown-ignore(CANON-03, reason="4 args on method only")',
    "  compute(a: number): number {",
    "    return a;",
    "  }",
    "}",
  ].join("\n");
  assert.ok(checkSource(badInterface, "state.ts").some((e) => e.rule === "CANON-03"));
  assert.ok(checkSource(badSvelteStore, "store.svelte.ts").some((e) => e.rule === "CANON-03"));
  assert.ok(checkSource(bareAndHooks, "hook.ts").some((e) => e.rule === "CANON-03"));
  assert.ok(checkSource(innerIgnoreDoesNotHideOuterClass, "outer.ts").some((e) => e.rule === "CANON-03"));
  assert.deepEqual(checkSource(suppressed, "legacy.ts"), []);
  assert.deepEqual(checkSource(cleanOrthogonal, "merchant.ts"), []);
});

test("stacked topdown-ignore above [L0] and JSDoc suppresses in heuristic and AST modes, and --outline requires AST", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const stacked = [
    '// topdown-ignore(CANON-05, reason="migrating [L1] ternary")',
    "// [L0] Entrypoint",
    "/** JSDoc comment */",
    "export function pick(a: boolean, b: boolean): string {",
    '  return a ? (b ? "x" : "y") : "z";',
    "}",
  ].join("\n");
  const noAdjacentLeak = [
    'export function first(): number { return 1; } // topdown-ignore(CANON-05, reason="only first")',
    "export function second(a: boolean, b: boolean, c: boolean): number {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return 2;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(stacked, "stacked.ts"), []);
  assert.ok(checkSource(noAdjacentLeak, "leak.ts").some((e) => e.rule === "CANON-05"));

  const { loadTypeScript, checkSourceWithAst } = await import("../enforcement/check_ts.mjs");
  const tsMod = await loadTypeScript();
  if (tsMod) {
    assert.deepEqual(checkSourceWithAst(stacked, "stacked.ts", tsMod), []);
    assert.ok(checkSourceWithAst(noAdjacentLeak, "leak.ts", tsMod).some((e) => e.rule === "CANON-05"));
    const multiStmtBadL3 = [
      "// [L3] Invalid leaf tag on multi-statement caller",
      "export function runFlow(amount: number): number {",
      "  const base = 10;",
      "  return addBase(amount + base);",
      "}",
      "function addBase(amount: number): number {",
      "  return amount + 1;",
      "}",
    ].join("\n");
    assert.ok(checkSourceWithAst(multiStmtBadL3, "bad_l3.ts", tsMod).some((e) => e.rule === "CANON-01"));

    const cleanFixture = path.join(rootDir, "examples", "fixtures", "ts_after.ts");
    const outlineProc = spawnSync(process.execPath, [checkerPath, "--outline", cleanFixture], {
      encoding: "utf8",
    });
    assert.equal(outlineProc.status, 0, outlineProc.stderr);
    assert.match(outlineProc.stdout, /\[L0\] processPayoutBatch \(L32-L43, 12L\) -> formatCentsUsd, settleEligiblePayouts/);
    assert.match(outlineProc.stdout, /\[L1\] settleEligiblePayouts \(L46-L65, 20L\) <- processPayoutBatch -> calculateFeeCents, formatCentsUsd/);
  }

  const cleanFixture = path.join(rootDir, "examples", "fixtures", "ts_after.ts");
  const heurOutline = spawnSync(
    process.execPath,
    [checkerPath, "--backend=heuristic", "--outline", cleanFixture],
    { encoding: "utf8" },
  );
  assert.equal(heurOutline.status, 2);
  assert.match(heurOutline.stderr, /AST|typescript|topdown/i);
});

test("same-line close and open function avoids false CANON-03, CANON-05, and ignore bleed", async () => {
  const { checkSource, loadTypeScript, checkSourceWithAst } = await import("../enforcement/check_ts.mjs");
  const sharedClean = [
    "export function first(): boolean {",
    "  const isLoading = true;",
    "  return isLoading;",
    "} export function second(): boolean {",
    "  const isError = false;",
    "  return isError;",
    "}",
  ].join("\n");
  const ignoreBleedNesting = [
    '// topdown-ignore(CANON-05, reason="only first")',
    "export function first(): boolean {",
    "  return true;",
    "} export function second(a: boolean, b: boolean, c: boolean): boolean {",
    "  if (a) {",
    "    if (b) {",
    "      if (c) {",
    "        return true;",
    "      }",
    "    }",
    "  }",
    "  return false;",
    "}",
  ].join("\n");
  const ignoreBleedState = [
    '// topdown-ignore(CANON-03, reason="only first")',
    "export function first(): boolean {",
    "  return true;",
    "} export function second(): boolean {",
    "  const isLoading = true;",
    "  const isError = false;",
    "  return isLoading || isError;",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(sharedClean, "shared.ts"), []);
  assert.deepEqual(
    checkSource(ignoreBleedNesting, "bleed05.ts").map((e) => e.rule),
    ["CANON-05"],
  );
  assert.deepEqual(
    checkSource(ignoreBleedState, "bleed03.ts").map((e) => e.rule),
    ["CANON-03"],
  );

  const tsMod = await loadTypeScript();
  if (tsMod) {
    assert.deepEqual(checkSourceWithAst(sharedClean, "shared.ts", tsMod), []);
    assert.deepEqual(
      checkSourceWithAst(ignoreBleedNesting, "bleed05.ts", tsMod).map((e) => e.rule),
      ["CANON-05"],
    );
    assert.deepEqual(
      checkSourceWithAst(ignoreBleedState, "bleed03.ts", tsMod).map((e) => e.rule),
      ["CANON-03"],
    );
  }
});

test("semicolon-less top-level declarations do not bleed CANON-03 and multi-line useState/signatures flush", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const semicolonLessNoBleed = [
    "const isLoading = false",
    "export function render(): boolean {",
    "  const isError = false",
    "  return isError",
    "}",
  ].join("\n");
  const semicolonLessCallNoBleed = [
    "const isLoading = computeFlag(",
    "  true,",
    ")",
    "export function render(): boolean {",
    "  const isError = false",
    "  return isError",
    "}",
  ].join("\n");
  const multilineSigPreserved = [
    "export function submit(",
    "  isLoading: boolean",
    "): string",
    "{",
    "  const isError = false",
    '  return isLoading || isError ? "err" : "ok"',
    "}",
  ].join("\n");
  const multilineUseState = [
    "export function useRequest(): { isLoading: boolean; isError: boolean } {",
    "  const [isLoading, setLoading] = useState(",
    "    false,",
    "  );",
    "  const [isError, setError] = useState(false);",
    "  return { isLoading, isError };",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(semicolonLessNoBleed, "no_bleed.ts"), []);
  assert.deepEqual(checkSource(semicolonLessCallNoBleed, "no_bleed_call.ts"), []);
  assert.ok(checkSource(multilineSigPreserved, "ml_sig.ts").some((e) => e.rule === "CANON-03"));
  assert.ok(checkSource(multilineUseState, "ml_hook.ts").some((e) => e.rule === "CANON-03"));
});

test("multi-line TS control headers, .catch, do-while, condition destructuring, and Svelte 5 $state<boolean>", async () => {
  const { checkSource } = await import("../enforcement/check_ts.mjs");
  const multilineIfDeep = [
    "export function deepIf(condA: boolean, condB: boolean, flagC: boolean, flagD: boolean): number {",
    "  if (",
    "    condA &&",
    "    condB",
    "  ) {",
    "    if (flagC) {",
    "      if (flagD) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const multilineIfState = [
    "export function stateIf(condA: boolean, condB: boolean): boolean {",
    "  const isLoading = true;",
    "  if (",
    "    condA &&",
    "    condB",
    "  ) {",
    "    const isError = true;",
    "    return isError;",
    "  }",
    "  return isLoading;",
    "}",
  ].join("\n");
  const promiseCatchAndDoWhile = [
    "export async function loadSafe(flagA: boolean, flagB: boolean): Promise<number> {",
    "  if (flagA) {",
    "    if (flagB) {",
    "      do {",
    "        step();",
    "      } while (",
    "        shouldRetry()",
    "      );",
    "      await fetchItems().catch(",
    "        (err) => handleErr(err),",
    "      );",
    "      return 1;",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const destructuringForLoopDepth2 = [
    "export function scanItems(flagA: boolean, items: Array<{ id: string }>): number {",
    "  if (flagA) {",
    "    for (const {",
    "      id",
    "    } of items) {",
    "      if (id.length > 0) {",
    "        return 1;",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const destructuringForLoopDepth2Clean = [
    "export function scanClean(flagA: boolean, items: Array<{ id: string }>): number {",
    "  if (flagA) {",
    "    for (const {",
    "      id",
    "    } of items) {",
    "      logId(id);",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const rustKeywordsIgnored = [
    "export function ok(flagA: boolean, flagB: boolean): number {",
    "  if (flagA) {",
    "    if (flagB) {",
    "      match {",
    "        loop {",
    "          return 1;",
    "        }",
    "      }",
    "    }",
    "  }",
    "  return 0;",
    "}",
  ].join("\n");
  const svelte5Generic = [
    "export class CheckoutStore {",
    "  isLoading = $state<boolean>(false);",
    "  isError = $state<boolean>(false);",
    "}",
  ].join("\n");
  assert.ok(checkSource(multilineIfDeep, "mif05.ts").some((e) => e.rule === "CANON-05"));
  assert.ok(checkSource(multilineIfState, "mif03.ts").some((e) => e.rule === "CANON-03"));
  assert.deepEqual(checkSource(promiseCatchAndDoWhile, "catch_dowhile.ts"), []);
  assert.ok(checkSource(destructuringForLoopDepth2, "for_destruct_deep.ts").some((e) => e.rule === "CANON-05"));
  assert.deepEqual(checkSource(destructuringForLoopDepth2Clean, "for_destruct_ok.ts"), []);
  assert.deepEqual(checkSource(rustKeywordsIgnored, "non_ts_kw.ts"), []);
  assert.ok(checkSource(svelte5Generic, "store.svelte.ts").some((e) => e.rule === "CANON-03"));
});

test("decorated TS methods and multi-line @Decorator({...}) headers suppress in heuristic and AST modes", async () => {
  const { checkSource, loadTypeScript, checkSourceWithAst } = await import("../enforcement/check_ts.mjs");
  const betweenDecorators = [
    "export class Handler {",
    "  @autobind",
    '  // topdown-ignore(CANON-05, reason="legacy nesting")',
    "  @Decorator({",
    '    path: "a)b}c]", // trailing ] ) }',
    "    items: [1, 2],",
    "  })",
    "  public async handle(a: boolean, b: boolean, c: boolean): Promise<number> {",
    "    if (a) {",
    "      if (b) {",
    "        if (c) {",
    "          return 1;",
    "        }",
    "      }",
    "    }",
    "    return 0;",
    "  }",
    "}",
  ].join("\n");
  const aboveDecoratorAfterPrev = [
    "export class Controller {",
    "  first(): number {",
    "    return 1;",
    "  }",
    '  // topdown-ignore(CANON-05, reason="legacy handler")',
    "  @Route({",
    '    method: "POST",',
    "  })",
    "  second(a: boolean, b: boolean, c: boolean): number {",
    "    if (a) {",
    "      if (b) {",
    "        if (c) {",
    "          return 1;",
    "        }",
    "      }",
    "    }",
    "    return 0;",
    "  }",
    "}",
  ].join("\n");
  assert.deepEqual(checkSource(betweenDecorators, "dec_between.ts"), []);
  assert.deepEqual(checkSource(aboveDecoratorAfterPrev, "dec_above.ts"), []);

  const tsMod = await loadTypeScript();
  if (tsMod) {
    assert.deepEqual(checkSourceWithAst(betweenDecorators, "dec_between.ts", tsMod), []);
    assert.deepEqual(checkSourceWithAst(aboveDecoratorAfterPrev, "dec_above.ts", tsMod), []);

    const decLines = [
      "export class LongDecorated {",
      "  @Dec1()",
      "  @Dec2()",
      "  @Dec3()",
      "  @Dec4()",
      "  @Dec5()",
      "  run(): number {",
      ...Array.from({ length: 76 }, (_, idx) => `    const step${idx} = ${idx};`),
      "    return step0;",
      "  }",
      "}",
    ].join("\n");
    assert.deepEqual(
      checkSourceWithAst(decLines, "dec_len.ts", tsMod).filter((e) => e.rule === "CANON-01"),
      [],
    );
  }
});

test("AST mode includes top-level const arrows, tier checks, duplicate names, outline JSON, and positive witness", async () => {
  const fs = await import("node:fs");
  const mod = await import("../enforcement/check_ts.mjs");
  const tsMod = await mod.loadTypeScript();
  if (!tsMod) {
    return;
  }

  const witnessPath = path.join(rootDir, "tests", "positive_witness", "ts_idioms.ts");
  const witnessSource = fs.readFileSync(witnessPath, "utf8");
  assert.deepEqual(mod.checkSourceWithAst(witnessSource, witnessPath, tsMod), []);

  const constArrowsOutOfOrder = [
    "// [L3] Leaf defined above caller",
    "const formatScore = (score: number): string => {",
    "  return `${score}pts`;",
    "};",
    "",
    "// [L0] Orchestrator",
    "export const",
    "  renderScore = (score: number): string => {",
    "    return formatScore(score);",
    "  };",
  ].join("\n");
  const arrowOrderViols = mod.checkSourceWithAst(constArrowsOutOfOrder, "arrows.ts", tsMod);
  assert.equal(
    arrowOrderViols.filter((e) => e.rule === "CANON-01" && e.message.includes("defined above it")).length,
    1,
  );
  const arrowOutline = mod.buildAstFileOutline(constArrowsOutOfOrder, "arrows.ts", tsMod);
  assert.deepEqual(
    arrowOutline.map((row) => row.name),
    ["formatScore", "renderScore"],
  );
  assert.equal(arrowOutline[1].start_line, 8);
  assert.equal(arrowOutline[1].declared_tier, "L0");
  assert.deepEqual(arrowOutline[1].callees, ["formatScore"]);
  assert.deepEqual(arrowOutline[0].callers, ["renderScore"]);

  const l0AdapterAndGap = [
    "// [L0] Adapter Entrypoint",
    "export function runAdapter(raw: string): string {",
    "  return writeLog(raw);",
    "}",
    "// [L0] Adapter Leaf",
    "function writeLog(raw: string): string {",
    "  return raw.trim();",
    "}",
    "// [L0] Gap Entrypoint",
    "export function runGap(raw: string): string {",
    "  return normalizeText(raw);",
    "}",
    "// [L3] Leaf",
    "function normalizeText(raw: string): string {",
    "  return raw.toLowerCase();",
    "}",
  ].join("\n");
  assert.deepEqual(mod.checkSourceWithAst(l0AdapterAndGap, "adapter_gap.ts", tsMod), []);

  const invalidChains = [
    "// [L1] Pure workflow",
    "export function workflow(count: number): number {",
    "  return badLeaf(count);",
    "}",
    "// [L0] Invalid L0 below L1",
    "function badLeaf(count: number): number {",
    "  return count + 1;",
    "}",
  ].join("\n");
  const singleDiag = mod.checkSourceWithAst(invalidChains, "inv1.ts", tsMod).filter((e) => e.rule === "CANON-01");
  assert.equal(singleDiag.length, 1);
  assert.equal(singleDiag[0].line, 6);

  const noL4Chain = [
    "// [L2] Start",
    "export function midStep(count: number): number {",
    "  return subStep(count);",
    "}",
    "// [L2] Mid",
    "function subStep(count: number): number {",
    "  return innerLeaf(count);",
    "}",
    "// [L2] End",
    "function innerLeaf(count: number): number {",
    "  return count + 1;",
    "}",
  ].join("\n");
  const noL4Viols = mod.checkSourceWithAst(noL4Chain, "nol4.ts", tsMod).filter((e) => e.rule === "CANON-01");
  assert.ok(noL4Viols.length > 0);
  for (const viol of noL4Viols) {
    assert.ok(!viol.message.includes("L4"), viol.message);
  }

  const dupTopNames = [
    "// [L2] First dup",
    "export function dupStep(count: number): number {",
    "  return badLeaf(count);",
    "}",
    "// [L0] Second dup",
    "export function dupStep(count: number): number {",
    "  return count;",
    "}",
    "// [L2] Callee below [L2]",
    "function badLeaf(count: number): number {",
    "  return count + 1;",
    "}",
  ].join("\n");
  const dupOutline = mod.buildAstFileOutline(dupTopNames, "dups.ts", tsMod);
  assert.deepEqual(
    dupOutline.map((row) => row.declared_tier),
    ["L2", "L0", "L2"],
  );
  assert.deepEqual(dupOutline[2].callers, ["dupStep"]);
  const dupViols = mod.checkSourceWithAst(dupTopNames, "dups.ts", tsMod).filter((e) => e.rule === "CANON-01");
  assert.ok(dupViols.some((e) => e.message.includes("below 'dupStep' ([L2])")));
  const dupFix = mod.fixSourceWithAst(
    "function dupStep(count: number): number { return count; }\nfunction dupStep(count: number): number { return count + 1; }\n",
    "dupfix.ts",
    tsMod,
  );
  assert.notEqual(dupFix.refusal, null);

  const cleanFixture = path.join(rootDir, "examples", "fixtures", "ts_after.ts");
  const outlineJsonProc = spawnSync(
    process.execPath,
    [checkerPath, "--outline", "--json", cleanFixture],
    { encoding: "utf8" },
  );
  assert.equal(outlineJsonProc.status, 0, outlineJsonProc.stderr);
  const parsedOutline = JSON.parse(outlineJsonProc.stdout);
  assert.equal(parsedOutline.length, 1);
  assert.equal(parsedOutline[0].functions[0].name, "processPayoutBatch");
  assert.deepEqual(parsedOutline[0].functions[0].valid_tiers, ["L0"]);
});

test("LLM-08 detects duplicate TS AST leaves and avoids false positives across semantic differences", async () => {
  const mod = await import("../enforcement/check_ts.mjs");
  const tsMod = await mod.loadTypeScript();
  if (!tsMod) {
    return;
  }

  const renamedDups = [
    "export function normalizeAlpha(rawText: string, rows: string[]): string[] {",
    "  const cleaned = rawText.trim();",
    "  const parts = cleaned.split(',');",
    "  const mapped = parts.map((entry) => entry.toLowerCase());",
    "  const [firstItem] = rows;",
    "  const combined = [...mapped, firstItem];",
    "  return combined.filter((token) => token.length > 0);",
    "}",
    "",
    "export const normalizeBeta = (inputStr: string, items: string[]): string[] => {",
    "  const trimmed = inputStr.trim();",
    "  const chunks = trimmed.split(',');",
    "  const lowered = chunks.map((row) => row.toLowerCase());",
    "  const [headEntry] = items;",
    "  const merged = [...lowered, headEntry];",
    "  return merged.filter((word) => word.length > 0);",
    "};",
  ].join("\n");
  assert.deepEqual(
    mod.checkSource(renamedDups, "heur.ts").filter((e) => e.rule === "LLM-08"),
    [],
  );
  const astDupViols = mod.checkSourceWithAst(renamedDups, "dups.ts", tsMod).filter((e) => e.rule === "LLM-08");
  assert.equal(astDupViols.length, 1);
  assert.equal(astDupViols[0].line, 10);
  assert.equal(
    astDupViols[0].message,
    "Function 'normalizeBeta' duplicates leaf helper 'normalizeAlpha' (line 1); reuse the existing helper instead of adding a duplicate.",
  );

  const suppressed = renamedDups.replace(
    "export const normalizeBeta",
    '// topdown-ignore(LLM-08, reason="separate domain")\nexport const normalizeBeta',
  );
  assert.deepEqual(
    mod.checkSourceWithAst(suppressed, "supp.ts", tsMod).filter((e) => e.rule === "LLM-08"),
    [],
  );

  const untaggedTwoTier = [
    "// [L0] Entry",
    "export function runFlow(firstText: string, secondText: string): [string, string] {",
    "  return [cleanAlpha(firstText), cleanBeta(secondText)];",
    "}",
    "function cleanAlpha(rawText: string): string {",
    "  const stepOne = rawText.trim();",
    "  const stepTwo = stepOne.toLowerCase();",
    "  const stepThree = stepTwo.replace(' ', '_');",
    "  const stepFour = stepThree.slice(1);",
    "  const stepFive = stepFour.toUpperCase();",
    "  return stepFive;",
    "}",
    "function cleanBeta(otherText: string): string {",
    "  const stepOne = otherText.trim();",
    "  const stepTwo = stepOne.toLowerCase();",
    "  const stepThree = stepTwo.replace(' ', '_');",
    "  const stepFour = stepThree.slice(1);",
    "  const stepFive = stepFour.toUpperCase();",
    "  return stepFive;",
    "}",
  ].join("\n");
  assert.equal(
    mod.checkSourceWithAst(untaggedTwoTier, "two_tier.ts", tsMod).filter((e) => e.rule === "LLM-08").length,
    1,
  );

  const shortWithKr = [
    "function shortOne(",
    "  rawText: string,",
    "): string {",
    "  // comment inside",
    "  const stepOne = rawText.trim();",
    "  const stepTwo = stepOne.toLowerCase();",
    "  const stepThree = stepTwo.replace('-', '_');",
    "  const stepFour = stepThree.toUpperCase();",
    "  return stepFour;",
    "}",
    "function shortTwo(",
    "  inputStr: string,",
    "): string {",
    "  // comment inside",
    "  const partOne = inputStr.trim();",
    "  const partTwo = partOne.toLowerCase();",
    "  const partThree = partTwo.replace('-', '_');",
    "  const partFour = partThree.toUpperCase();",
    "  return partFour;",
    "}",
  ].join("\n");
  assert.deepEqual(mod.checkSourceWithAst(shortWithKr, "short.ts", tsMod), []);

  const l0Exempt = [
    "// [L0] Adapter A",
    "export function fetchAlpha(rawText: string): string {",
    "  const stepOne = rawText.trim();",
    "  const stepTwo = stepOne.toLowerCase();",
    "  const stepThree = stepTwo.replace(' ', '_');",
    "  const stepFour = stepThree.slice(1);",
    "  const stepFive = stepFour.toUpperCase();",
    "  return stepFive;",
    "}",
    "// [L0] Adapter B",
    "export function fetchBeta(otherText: string): string {",
    "  const stepOne = otherText.trim();",
    "  const stepTwo = stepOne.toLowerCase();",
    "  const stepThree = stepTwo.replace(' ', '_');",
    "  const stepFour = stepThree.slice(1);",
    "  const stepFive = stepFour.toUpperCase();",
    "  return stepFive;",
    "}",
  ].join("\n");
  assert.deepEqual(mod.checkSourceWithAst(l0Exempt, "l0.ts", tsMod), []);

  const nonMatchingCases = [
    // swapped params
    [
      "function subAb(first: number, second: number): number {",
      "  const valA = first + 1;",
      "  const valB = second + 2;",
      "  const valC = valA * 3;",
      "  const valD = valB * 4;",
      "  const diff = valC - valD;",
      "  return diff;",
      "}",
      "function subBa(first: number, second: number): number {",
      "  const valA = second + 1;",
      "  const valB = first + 2;",
      "  const valC = valA * 3;",
      "  const valD = valB * 4;",
      "  const diff = valC - valD;",
      "  return diff;",
      "}",
    ].join("\n"),
    // different defaults
    [
      "function defOne(amount = 1): number {",
      "  const stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
      "function defTwo(amount = 2): number {",
      "  const stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
    ].join("\n"),
    // async vs sync
    [
      "function syncFn(amount: number): number {",
      "  const stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
      "async function asyncFn(amount: number): Promise<number> {",
      "  const stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
    ].join("\n"),
    // !x vs -x
    [
      "function negateBool(flag: number): boolean {",
      "  const stepOne = !flag;",
      "  const stepTwo = Boolean(stepOne);",
      "  const stepThree = stepTwo === true;",
      "  const stepFour = stepThree ? 1 : 0;",
      "  const stepFive = stepFour > 0;",
      "  return stepFive;",
      "}",
      "function negateNum(flag: number): boolean {",
      "  const stepOne = -flag;",
      "  const stepTwo = Boolean(stepOne);",
      "  const stepThree = stepTwo === true;",
      "  const stepFour = stepThree ? 1 : 0;",
      "  const stepFive = stepFour > 0;",
      "  return stepFive;",
      "}",
    ].join("\n"),
    // x++ vs x--
    [
      "function incFn(amount: number): number {",
      "  let count = amount;",
      "  count++;",
      "  const stepTwo = count + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  return stepFour;",
      "}",
      "function decFn(amount: number): number {",
      "  let count = amount;",
      "  count--;",
      "  const stepTwo = count + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  return stepFour;",
      "}",
    ].join("\n"),
    // a + b vs a - b
    [
      "function addFn(first: number, second: number): number {",
      "  const stepOne = first + second;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
      "function subFn(first: number, second: number): number {",
      "  const stepOne = first - second;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
    ].join("\n"),
    // const vs let
    [
      "function constFn(amount: number): number {",
      "  const stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
      "function letFn(amount: number): number {",
      "  let stepOne = amount + 1;",
      "  const stepTwo = stepOne + 2;",
      "  const stepThree = stepTwo * 3;",
      "  const stepFour = stepThree - 4;",
      "  const stepFive = stepFour + 5;",
      "  return stepFive;",
      "}",
    ].join("\n"),
    // { name } vs { code } shorthand & binding
    [
      "function bindName(payload: { name: string; code: string }): { name: string } {",
      "  const { name } = payload;",
      "  const stepTwo = name.trim();",
      "  const stepThree = stepTwo.toLowerCase();",
      "  const stepFour = stepThree.slice(0, 4);",
      "  const stepFive = stepFour.toUpperCase();",
      "  return { name: stepFive };",
      "}",
      "function bindCode(payload: { name: string; code: string }): { code: string } {",
      "  const { code } = payload;",
      "  const stepTwo = code.trim();",
      "  const stepThree = stepTwo.toLowerCase();",
      "  const stepFour = stepThree.slice(0, 4);",
      "  const stepFive = stepFour.toUpperCase();",
      "  return { code: stepFive };",
      "}",
    ].join("\n"),
    // shorthand return { name } vs { code }
    [
      "function retName(rawText: string): { name: string } {",
      "  const stepOne = rawText.trim();",
      "  const stepTwo = stepOne.toLowerCase();",
      "  const stepThree = stepTwo.replace(' ', '_');",
      "  const stepFour = stepThree.slice(1);",
      "  const name = stepFour.toUpperCase();",
      "  return { name };",
      "}",
      "function retCode(rawText: string): { code: string } {",
      "  const stepOne = rawText.trim();",
      "  const stepTwo = stepOne.toLowerCase();",
      "  const stepThree = stepTwo.replace(' ', '_');",
      "  const stepFour = stepThree.slice(1);",
      "  const code = stepFour.toUpperCase();",
      "  return { code };",
      "}",
    ].join("\n"),
    // nested const { a: { b } } = obj
    [
      "function nestedBindB(payload: { a: { b: string; c: string } }): string {",
      "  const { a: { b } } = payload;",
      "  const stepTwo = b.trim();",
      "  const stepThree = stepTwo.toLowerCase();",
      "  const stepFour = stepThree.slice(0, 4);",
      "  const stepFive = stepFour.toUpperCase();",
      "  return stepFive;",
      "}",
      "function nestedBindC(payload: { a: { b: string; c: string } }): string {",
      "  const { a: { c } } = payload;",
      "  const stepTwo = c.trim();",
      "  const stepThree = stepTwo.toLowerCase();",
      "  const stepFour = stepThree.slice(0, 4);",
      "  const stepFive = stepFour.toUpperCase();",
      "  return stepFive;",
      "}",
    ].join("\n"),
  ];
  for (let idx = 0; idx < nonMatchingCases.length; idx += 1) {
    const viols = mod.checkSourceWithAst(nonMatchingCases[idx], `nm_${idx}.ts`, tsMod).filter((e) => e.rule === "LLM-08");
    assert.deepEqual(viols, [], `Unexpected LLM-08 on case ${idx}`);
  }
});

test("2-checkpoint TypeScript AST evolution detects compounding erosion and passes clean refactor", async () => {
  const mod = await import("../enforcement/check_ts.mjs");
  const tsMod = await mod.loadTypeScript();
  if (!tsMod) {
    return;
  }

  const cp1 = [
    "// [L0] Orchestrator",
    "export function processBatch(amountCents: number): string {",
    "  const netCents = computeNetCents(amountCents);",
    "  return formatUsdCents(netCents);",
    "}",
    "// [L1] Pure Rule",
    "function computeNetCents(amountCents: number): number {",
    "  return amountCents - Math.floor(amountCents * 0.02);",
    "}",
    "// [L3] Leaf",
    "function formatUsdCents(amountCents: number): string {",
    "  const absCents = Math.abs(amountCents);",
    "  const whole = Math.floor(absCents / 100);",
    "  const cents = String(absCents % 100).padStart(2, '0');",
    "  const prefix = amountCents < 0 ? '-' : '';",
    "  const body = `${whole}.${cents}`;",
    "  return `${prefix}$${body}`;",
    "}",
  ].join("\n");

  const cp2Naive = [
    "// [L3] Moved above caller",
    "function formatUsdCents(amountCents: number): string {",
    "  const absCents = Math.abs(amountCents);",
    "  const whole = Math.floor(absCents / 100);",
    "  const cents = String(absCents % 100).padStart(2, '0');",
    "  const prefix = amountCents < 0 ? '-' : '';",
    "  const body = `${whole}.${cents}`;",
    "  return `${prefix}$${body}`;",
    "}",
    "// [L0] Orchestrator with lifecycle booleans",
    "export function processBatch(amountCents: number): string {",
    "  const isLoading = false;",
    "  const isError = false;",
    "  if (isLoading || isError) {",
    "    return formatUsdCents(amountCents);",
    "  }",
    "  return formatEurCents(amountCents);",
    "}",
    "// [L3] Duplicate leaf primitive",
    "function formatEurCents(centsTotal: number): string {",
    "  const magCents = Math.abs(centsTotal);",
    "  const units = Math.floor(magCents / 100);",
    "  const frac = String(magCents % 100).padStart(2, '0');",
    "  const sign = centsTotal < 0 ? '-' : '';",
    "  const text = `${units}.${frac}`;",
    "  return `${sign}$${text}`;",
    "}",
  ].join("\n");

  const cp2Clean = [
    "// [L0] Orchestrator",
    "export function processBatch(amountCents: number, currencySymbol: string): string {",
    "  const netCents = computeNetCents(amountCents);",
    "  return formatMoneyCents(netCents, currencySymbol);",
    "}",
    "// [L1] Pure Rule",
    "function computeNetCents(amountCents: number): number {",
    "  return amountCents - Math.floor(amountCents * 0.02);",
    "}",
    "// [L3] Parameterized Leaf",
    "function formatMoneyCents(amountCents: number, currencySymbol: string): string {",
    "  const absCents = Math.abs(amountCents);",
    "  const whole = Math.floor(absCents / 100);",
    "  const cents = String(absCents % 100).padStart(2, '0');",
    "  const prefix = amountCents < 0 ? '-' : '';",
    "  const body = `${whole}.${cents}`;",
    "  return `${prefix}${currencySymbol}${body}`;",
    "}",
  ].join("\n");

  assert.deepEqual(mod.checkSourceWithAst(cp1, "cp1.ts", tsMod), []);
  const cp1Outline = mod.buildAstFileOutline(cp1, "cp1.ts", tsMod);
  assert.deepEqual(cp1Outline.map((r) => r.name), ["processBatch", "computeNetCents", "formatUsdCents"]);

  const naiveRules = new Set(mod.checkSourceWithAst(cp2Naive, "cp2_naive.ts", tsMod).map((e) => e.rule));
  assert.deepEqual(naiveRules, new Set(["CANON-01", "CANON-03", "LLM-08"]));

  assert.deepEqual(mod.checkSourceWithAst(cp2Clean, "cp2_clean.ts", tsMod), []);
  const cp2Outline = mod.buildAstFileOutline(cp2Clean, "cp2_clean.ts", tsMod);
  assert.deepEqual(cp2Outline.map((r) => r.name), ["processBatch", "computeNetCents", "formatMoneyCents"]);
});

test("CANON-03 flags deeply nested inline generics and wide anonymous tuples in AST mode only", async () => {
  const mod = await import("../enforcement/check_ts.mjs");
  const tsMod = await mod.loadTypeScript();
  if (!tsMod) {
    return;
  }
  const badCode = [
    "export function deepMatrix(",
    "  matrix: Record<string, Map<string, Array<number>>>,",
    "): [string, number, boolean, string, number] {",
    "  const row: [number, number, number, number] = [1, 2, 3, 4];",
    '  return ["ok", row[0], true, "v", 5];',
    "}",
    "",
    '// topdown-ignore(CANON-03, reason="external wire format")',
    "export function suppressedTuple(): [string, number, boolean, number] {",
    '  return ["ok", 1, true, 2];',
    "}",
  ].join("\n");
  const astViolations = mod.checkSourceWithAst(badCode, "types.ts", tsMod).filter((v) => v.rule === "CANON-03");
  assert.equal(astViolations.length, 3, JSON.stringify(astViolations));
  assert.ok(astViolations.some((v) => v.message.includes("nesting depth 3")));
  assert.ok(astViolations.some((v) => v.message.includes("5 elements")));
  assert.ok(astViolations.some((v) => v.message.includes("4 elements")));
  assert.equal(mod.checkSource(badCode, "types.ts").filter((v) => v.rule === "CANON-03").length, 0);

  const goodCode = [
    "type DeepMap = Record<string, Map<string, Array<number>>>;",
    "type WideRow = [number, number, number, number];",
    "",
    "export async function cleanSignatures(",
    "  grouped: Record<string, number[]>,",
    "  pairs: Array<[string, number]>,",
    "  maybe: Record<string, number[]> | null,",
    "): Promise<Readonly<Record<string, number[]>>> {",
    "  const triple: [string, number, boolean] = ['a', 1, true];",
    "  return maybe ?? grouped;",
    "}",
  ].join("\n");
  const goodViolations = mod.checkSourceWithAst(goodCode, "clean_types.ts", tsMod).filter((v) => v.rule === "CANON-03");
  assert.deepEqual(goodViolations, []);
});

test("CANON-01 module size ceiling, preamble waiver, and LLM-05 horizontal packing", async () => {
  const mod = await import("../enforcement/check_ts.mjs");
  const tsMod = await mod.loadTypeScript();
  const monolith = Array.from({ length: 16 }, (_, idx) => {
    const body = Array.from({ length: 28 }, (_, step) => `  const step_${step} = value + ${step};`).join("\n");
    return `export function fn_${idx}(value: number): number {\n${body}\n  return value;\n}`;
  }).join("\n");
  assert.equal(mod.checkSource(monolith, "src/big.ts").filter((v) => v.rule === "CANON-01" && v.line === 1).length, 1);
  assert.equal(mod.checkSource(monolith, "tests/big.test.ts").filter((v) => v.rule === "CANON-01" && v.line === 1).length, 0);
  const waived = `// topdown-ignore(CANON-01, reason="standalone CLI")\n${monolith}`;
  assert.equal(mod.checkSource(waived, "src/big.ts").filter((v) => v.rule === "CANON-01" && v.line === 1).length, 0);

  const packed = [
    "export function cramped(flagA: boolean, flagB: boolean): number {",
    "  let total = 0; total += 1;",
    "  for (let idx = 0; idx < 3; idx += 1) {",
    "    total += idx;",
    "  }",
    "  const pair = [flagA ? 1 : 2, flagB ? 3 : 4];",
    "  return total + pair[0];",
    "}",
  ].join("\n");
  assert.equal(mod.checkSource(packed, "src/packed.ts").filter((v) => v.rule === "LLM-05").length, 1);
  if (tsMod) {
    assert.equal(mod.checkSourceWithAst(packed, "src/packed.ts", tsMod).filter((v) => v.rule === "LLM-05").length, 2);
  }
});
