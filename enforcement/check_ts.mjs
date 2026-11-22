/**
 * Lightweight zero-dependency heuristic TS/JS structural checker for topdown.
 * Intended as a complement to ESLint (see languages/typescript.md), not a full AST parser.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const MAX_FUNCTION_LINES = 80;
const MAX_NESTING_DEPTH = 2;
const MAX_POSITIONAL_ARGS = 3;
const MAX_BUSINESS_DEPTH = 2;
const VAGUE_NOUNS = new Set(["data", "info", "item", "temp", "result", "handler"]);
const GAMING_NAME_PATTERN = /(?:_part\d+|_helper\d*|Part\d+|Helper\d*)$/;
const TS_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const SKIP_DIRS = new Set(["node_modules", ".git", "dist", "build", ".svelte-kit", ".next"]);
const IGNORE_PATTERN = /\/\/\s*topdown-ignore(?:\((.*)\))?/;
const REASON_PATTERN = /reason\s*=\s*(?:"([^"]+)"|'([^']+)')/;
const RULE_ID_PATTERN = /\b(?:CANON|LLM)-\d{2}\b/g;
const FUNC_DECL_PATTERN = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b/;
const ARROW_DECL_PATTERN = /^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$]\w*\s*=\s*(?:async\s*)?\(/;
const CONTROL_OPEN_PATTERN = /\b(?:if|for|while|switch|try)\b[^{]*\{|(?<!\.)\bcatch\b[^{]*\{|\belse\s*\{/;
const EMPTY_CATCH_FULL_PATTERN = /\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}/g;
const TEST_RUNNER_BRANCH_PATTERN =
  /\bprocess\.env\.(?:VITEST|JEST_WORKER_ID)\b|\bprocess\.env\.NODE_ENV\s*===?\s*["']test["']/;
const TEST_PATH_PATTERN =
  /(?:^|[\\/])(?:tests?|__tests__)[\\/]|\.(?:test|spec)\.[a-z]+$/i;
const LIFECYCLE_BOOL_DECL_PATTERN =
  /\b((?:is|has|was)(?:Loading|Pending|Fetching|Submitting|Saving|Retrying|Error|Errored|Failed|Rejected|Success|Succeeded|Loaded|Ready|Done|Completed|Idle|_(?:loading|pending|fetching|submitting|saving|retrying|error|errored|failed|rejected|success|succeeded|loaded|ready|done|completed|idle))|(?:loading|pending|fetching|submitting|errored|failed|succeeded))\b(?:\s*\??\s*:\s*boolean\b|\s*=\s*(?:true|false|\$state\s*\(\s*(?:true|false)\s*\)))|\[\s*((?:is|has|was)(?:Loading|Pending|Fetching|Submitting|Saving|Retrying|Error|Errored|Failed|Rejected|Success|Succeeded|Loaded|Ready|Done|Completed|Idle|_(?:loading|pending|fetching|submitting|saving|retrying|error|errored|failed|rejected|success|succeeded|loaded|ready|done|completed|idle))|(?:loading|pending|fetching|submitting|errored|failed|succeeded))\s*,[^\]]*\]\s*=\s*(?:React\.)?useState\b/g;
const REGEX_PRECEDING_CHARS = new Set(["", "(", "[", "{", ",", ":", ";", "=", "!", "&", "|", "?", "+", "-", "*", "%", "~", "^", ">", "\n"]);
const REGEX_PRECEDING_KEYWORDS = new Set(["return", "case", "default", "throw", "in", "of", "typeof", "void", "delete", "new", "yield", "await"]);

async function main() {
  const options = parseCliArgs(process.argv.slice(2));
  if (!options) {
    process.stderr.write(
      "Usage: node check_ts.mjs [--json] [--backend=auto|ast|heuristic] [--show-backend] [--fix] [--dry-run] <path> [...]\n",
    );
    return 2;
  }
  const tsMod = options.backend === "heuristic" ? null : await loadTypeScript();
  if (options.backend === "ast" && !tsMod) {
    process.stderr.write("Error: --backend=ast requested, but 'typescript' package is not installed.\n");
    return 2;
  }
  if (options.applyFix && !tsMod) {
    process.stderr.write("Error: --fix requires the optional 'typescript' AST backend (heuristic mode cannot safely rewrite source).\n");
    return 2;
  }
  const activeBackend = tsMod ? "ast" : "heuristic";
  if (options.showBackend) {
    process.stderr.write(`backend: ${activeBackend}\n`);
  }
  const collected = collectTargetFiles(options.paths);
  if (collected.error) {
    process.stderr.write(`${collected.error}\n`);
    return 2;
  }
  const allViolations = [];
  for (const filePath of collected.files) {
    const outcome = processTargetFile(filePath, options, tsMod);
    if (outcome.error) {
      process.stderr.write(`${outcome.error}\n`);
      return 2;
    }
    allViolations.push(...outcome.violations);
  }
  if (!options.dryRun) {
    emitViolations(allViolations, options.asJson);
  }
  return allViolations.length > 0 ? 1 : 0;
}

function processTargetFile(filePath, options, tsMod) {
  let source;
  try {
    source = fs.readFileSync(filePath, "utf8");
  } catch (err) {
    return { violations: [], error: `Error reading ${filePath}: ${err.message}` };
  }
  if (options.applyFix && tsMod) {
    const fixed = fixSourceWithAst(source, filePath, tsMod);
    if (fixed.refusal) {
      process.stderr.write(`${filePath}: ${fixed.refusal}\n`);
    } else if (fixed.source !== source && options.dryRun) {
      process.stdout.write(`--- a/${filePath}\n+++ b/${filePath}\n${fixed.source}`);
      return { violations: [], error: null };
    } else if (fixed.source !== source) {
      fs.writeFileSync(filePath, fixed.source, "utf8");
      source = fixed.source;
    }
  }
  const violations = tsMod ? checkSourceWithAst(source, filePath, tsMod) : checkSource(source, filePath);
  return { violations, error: null };
}

export async function loadTypeScript() {
  try {
    const loaded = await import("typescript");
    return loaded.default ?? loaded;
  } catch {
    return null;
  }
}

export function checkSourceWithAst(source, filePath, tsMod) {
  const isJsx = /\.[jt]sx$/i.test(filePath);
  const { cleaned, comments } = lexSource(source, isJsx);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const sf = tsMod.createSourceFile(filePath, source, tsMod.ScriptTarget.Latest, true);
  const topFunctions = collectTopLevelAstFunctions(sf, tsMod);
  const memberFunctions = collectAstMemberFunctions(sf, tsMod);
  const allFunctions = [...topFunctions, ...memberFunctions];
  const rawViolations = [
    ...checkAstOrderAndDepth(topFunctions, sf, tsMod, filePath),
    ...checkAstFunctionRules(allFunctions, sf, tsMod, filePath),
    ...checkImplicitStateMachines(cleaned.split(/\r?\n/), filePath, directives),
    ...checkTestRunnerBranching(source, cleaned.split(/\r?\n/), filePath),
  ];
  const spans = allFunctions.map((entry) => ({ startLine: entry.startLine, endLine: entry.endLine }));
  const kept = filterSuppressed(rawViolations, spans, directives);
  return [...ignoreViolations, ...kept].sort(
    (left, right) => left.line - right.line || left.rule.localeCompare(right.rule),
  );
}

export function fixSourceWithAst(source, filePath, tsMod) {
  const sf = tsMod.createSourceFile(filePath, source, tsMod.ScriptTarget.Latest, true);
  const fnPositions = [];
  for (let idx = 0; idx < sf.statements.length; idx += 1) {
    const stmt = sf.statements[idx];
    if (tsMod.isFunctionDeclaration(stmt) && !stmt.body) {
      return { source, refusal: "Refused --fix: function overload signatures detected." };
    }
    if (tsMod.isFunctionDeclaration(stmt) && stmt.name && stmt.body) {
      fnPositions.push({ idx, stmt, name: stmt.name.text });
    }
  }
  if (fnPositions.length < 2) {
    return { source, refusal: null };
  }
  const firstPos = fnPositions[0].idx;
  const lastPos = fnPositions[fnPositions.length - 1].idx;
  if (lastPos - firstPos + 1 !== fnPositions.length) {
    return { source, refusal: "Refused --fix: interleaved statements or non-hoisted declarations between top-level functions." };
  }
  const nameSet = new Set(fnPositions.map((entry) => entry.name));
  const callGraph = new Map(fnPositions.map((entry) => [entry.name, collectAstCalls(entry.stmt, nameSet, tsMod)]));
  const ordered = topoSortAstFunctions(fnPositions, callGraph);
  if (!ordered) {
    return { source, refusal: "Refused --fix: cyclic/mutual recursion detected in local call graph." };
  }
  const isAlreadySorted = ordered.every((value, idx) => value === idx);
  if (isAlreadySorted) {
    return { source, refusal: null };
  }
  const sourceLines = source.split(/\r?\n/);
  const spans = buildSafeTsFunctionSpans(fnPositions, sf, sourceLines);
  if (!spans) {
    return { source, refusal: "Refused --fix: detached or block comments between functions would be lost." };
  }
  const prefix = sourceLines.slice(0, spans[0].startLine).join("\n");
  const suffix = sourceLines.slice(spans[spans.length - 1].endLine).join("\n");
  const blocks = ordered.map((idx) => sourceLines.slice(spans[idx].startLine, spans[idx].endLine).join("\n").trimEnd());
  const lead = prefix.trimEnd() ? `${prefix.trimEnd()}\n\n` : "";
  const tail = suffix.trim() ? `\n\n${suffix.trimStart()}` : "\n";
  return { source: `${lead}${blocks.join("\n\n")}${tail}`, refusal: null };
}

function buildSafeTsFunctionSpans(fnPositions, sf, sourceLines) {
  const spans = [];
  for (let idx = 0; idx < fnPositions.length; idx += 1) {
    const startLine = sf.getLineAndCharacterOfPosition(fnPositions[idx].stmt.getStart(sf)).line;
    const prevEndLine = idx > 0 ? sf.getLineAndCharacterOfPosition(fnPositions[idx - 1].stmt.getEnd()).line + 1 : 0;
    if (idx > 0 && startLine < prevEndLine) {
      return null;
    }
    let blockStartLine = startLine;
    while (
      blockStartLine > prevEndLine &&
      sourceLines[blockStartLine - 1].trim().startsWith("//") &&
      !sourceLines[blockStartLine - 1].trim().startsWith("///") &&
      !(blockStartLine - 1 === 0 && /@ts-|eslint-disable|copyright|license/i.test(sourceLines[0]))
    ) {
      blockStartLine -= 1;
    }
    if (idx > 0 && sourceLines.slice(prevEndLine, blockStartLine).some((line) => line.trim().length > 0)) {
      return null;
    }
    const endLine = sf.getLineAndCharacterOfPosition(fnPositions[idx].stmt.getEnd()).line + 1;
    spans.push({ startLine: blockStartLine, endLine });
  }
  return spans;
}

export function checkSource(source, filePath) {
  const isJsx = /\.[jt]sx$/i.test(filePath);
  const { cleaned, comments } = lexSource(source, isJsx);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const lines = cleaned.split(/\r?\n/);
  const functions = extractFunctions(lines);
  const rawViolations = [
    ...checkFunctionBlocks(functions, lines, filePath),
    ...checkTernariesAndEmptyCatches(cleaned, lines, filePath),
    ...checkImplicitStateMachines(lines, filePath, directives),
    ...checkTestRunnerBranching(source, lines, filePath),
  ];
  const kept = filterSuppressed(rawViolations, functions, directives);
  return [...ignoreViolations, ...kept].sort(
    (left, right) => left.line - right.line || left.rule.localeCompare(right.rule),
  );
}

function checkFunctionBlocks(functions, lines, filePath) {
  const violations = [];
  for (const fnSpan of functions) {
    const length = fnSpan.endLine - fnSpan.startLine + 1;
    if (length > MAX_FUNCTION_LINES) {
      violations.push({
        path: filePath,
        line: fnSpan.startLine,
        rule: "CANON-01",
        message: `Function starting at line ${fnSpan.startLine} is ${length} lines (max ${MAX_FUNCTION_LINES}) -> extract a cohesive L1/L2 pure helper.`,
      });
    }
    const maxNesting = measureControlNesting(lines, fnSpan.bodyStartLine, fnSpan.endLine);
    if (maxNesting > MAX_NESTING_DEPTH) {
      violations.push({
        path: filePath,
        line: fnSpan.startLine,
        rule: "CANON-05",
        message: `Control-flow nesting depth ${maxNesting} (max ${MAX_NESTING_DEPTH}) -> invert with early return/continue or extract an L2 predicate.`,
      });
    }
    violations.push(...findNestedFunctions(lines, fnSpan.bodyStartLine, fnSpan.endLine, filePath));
  }
  return violations;
}

function checkTernariesAndEmptyCatches(cleaned, lines, filePath) {
  const violations = [];
  for (const match of cleaned.matchAll(EMPTY_CATCH_FULL_PATTERN)) {
    const lineNo = countChar(cleaned.slice(0, match.index), "\n") + 1;
    violations.push({
      path: filePath,
      line: lineNo,
      rule: "LLM-02",
      message: "Empty catch block silently swallows errors -> narrow and handle the error or propagate to L0.",
    });
  }
  for (let idx = 0; idx < lines.length; idx += 1) {
    if (hasNestedTernary(lines[idx])) {
      violations.push({
        path: filePath,
        line: idx + 1,
        rule: "CANON-05",
        message: "Nested ternary expression is banned -> replace with flat if/return guard clauses.",
      });
    }
  }
  return violations;
}

function checkImplicitStateMachines(lines, filePath, directives) {
  const violations = [];
  const scopeStack = [];
  const closedScopes = [];
  let parenDepth = 0;
  let pendingStart = -1;
  let pendingFlags = [];
  for (let idx = 0; idx < lines.length; idx += 1) {
    const lineText = lines[idx];
    const lineNo = idx + 1;
    const trimmed = lineText.trim();
    if (pendingStart === -1 && trimmed.length > 0) {
      pendingStart = lineNo;
    }
    const matched = [...lineText.matchAll(LIFECYCLE_BOOL_DECL_PATTERN)].map((m) => m[1] ?? m[2]);
    parenDepth = Math.max(0, parenDepth + countChar(lineText, "(") - countChar(lineText, ")"));
    const opens = countChar(lineText, "{");
    const closes = countChar(lineText, "}");
    const isControl = CONTROL_OPEN_PATTERN.test(lineText);
    for (let k = 0; k < opens; k += 1) {
      scopeStack.push({
        startLine: pendingStart !== -1 ? pendingStart : lineNo,
        openLine: lineNo,
        flags: [...pendingFlags],
        isControl,
      });
      pendingStart = -1;
      pendingFlags = [];
    }
    if (scopeStack.length > 0 && parenDepth === 0) {
      scopeStack[scopeStack.length - 1].flags.push(...matched);
    } else {
      pendingFlags.push(...matched);
    }
    const popCount = Math.min(closes, scopeStack.length);
    for (let k = 0; k < popCount; k += 1) {
      const popped = scopeStack.pop();
      const targetFlags = popped.isControl && scopeStack.length > 0 ? scopeStack[scopeStack.length - 1].flags : popped.flags;
      targetFlags.push(...(targetFlags !== popped.flags ? popped.flags : []));
      closedScopes.push({ ...popped, flags: popped.isControl ? [] : popped.flags });
    }
    if (opens === 0 && closes === 0 && parenDepth === 0 && trimmed.endsWith(";")) {
      pendingStart = -1;
      pendingFlags = [];
    }
  }
  for (const scope of closedScopes) {
    const uniqueFlags = [...new Set(scope.flags)];
    if (uniqueFlags.length >= 2 && !isScopeSuppressed(scope, directives)) {
      violations.push({
        path: filePath,
        line: scope.openLine,
        rule: "CANON-03",
        message: `Implicit state machine (${uniqueFlags.join(", ")}) -> replace parallel lifecycle booleans with an explicit discriminated union.`,
      });
    }
  }
  return violations;
}

function isScopeSuppressed(scope, directives) {
  for (let lineNo = scope.startLine - 1; lineNo <= scope.openLine; lineNo += 1) {
    const directive = directives.get(lineNo);
    if (directive && directive.hasReason && (directive.isWildcard || directive.rules.has("CANON-03"))) {
      return true;
    }
  }
  return false;
}

function checkTestRunnerBranching(source, cleanedLines, filePath) {
  if (TEST_PATH_PATTERN.test(filePath)) {
    return [];
  }
  const violations = [];
  const rawLines = source.split(/\r?\n/);
  for (let idx = 0; idx < rawLines.length; idx += 1) {
    const cleanedLine = cleanedLines[idx] ?? "";
    const hasEnvInCode = /\bprocess\.env\b/.test(cleanedLine);
    if (hasEnvInCode && TEST_RUNNER_BRANCH_PATTERN.test(rawLines[idx])) {
      violations.push({
        path: filePath,
        line: idx + 1,
        rule: "LLM-07",
        message: "Test-runner env branching in production code -> inject test behavior via explicit L0 parameters.",
      });
    }
  }
  return violations;
}

function parseCliArgs(rawArgs) {
  let asJson = false;
  let applyFix = false;
  let dryRun = false;
  let showBackend = false;
  let backend = "auto";
  const paths = [];
  for (const token of rawArgs) {
    if (token === "--json") {
      asJson = true;
      continue;
    }
    if (token === "--fix") {
      applyFix = true;
      continue;
    }
    if (token === "--dry-run") {
      dryRun = true;
      continue;
    }
    if (token === "--show-backend") {
      showBackend = true;
      continue;
    }
    if (token.startsWith("--backend=") && ["auto", "ast", "heuristic"].includes(token.slice("--backend=".length))) {
      backend = token.slice("--backend=".length);
      continue;
    }
    if (token.startsWith("-")) {
      return null;
    }
    paths.push(token);
  }
  return paths.length > 0 ? { paths, asJson, applyFix, dryRun, showBackend, backend } : null;
}

function collectTargetFiles(rawPaths) {
  const files = [];
  const queue = [...rawPaths];
  while (queue.length > 0) {
    const current = queue.shift();
    let stats;
    try {
      stats = fs.statSync(current);
    } catch (err) {
      return { files: [], error: `Path error (${current}): ${err.message}` };
    }
    if (!stats.isDirectory()) {
      files.push(current);
      continue;
    }
    const entries = fs.readdirSync(current, { withFileTypes: true });
    queue.push(
      ...entries
        .filter((entry) => entry.isDirectory() && !SKIP_DIRS.has(entry.name))
        .map((entry) => path.join(current, entry.name)),
    );
    files.push(
      ...entries
        .filter(
          (entry) =>
            entry.isFile() &&
            TS_EXTENSIONS.has(path.extname(entry.name)) &&
            !entry.name.endsWith(".d.ts"),
        )
        .map((entry) => path.join(current, entry.name)),
    );
  }
  return { files: files.sort(), error: null };
}

function emitViolations(violations, asJson) {
  if (asJson) {
    process.stdout.write(`${JSON.stringify(violations, null, 2)}\n`);
    return;
  }
  for (const entry of violations) {
    process.stdout.write(`${entry.path}:${entry.line}: [${entry.rule}] ${entry.message}\n`);
  }
}

function parseIgnoreDirectives(comments, filePath) {
  const directives = new Map();
  const ignoreViolations = [];
  for (const comment of comments) {
    const match = IGNORE_PATTERN.exec(comment.text);
    if (!match) {
      continue;
    }
    const inner = match[1] ?? "";
    const rules = new Set(inner.match(RULE_ID_PATTERN) ?? []);
    const hasReason = REASON_PATTERN.test(inner);
    const isWildcard = inner.replace(REASON_PATTERN, "").replace(/[,\s]/g, "").length === 0;
    directives.set(comment.line, { line: comment.line, rules, hasReason, isWildcard });
    if (!hasReason) {
      ignoreViolations.push({
        path: filePath,
        line: comment.line,
        rule: "CANON-08",
        message: 'topdown-ignore requires a non-empty reason="...".',
      });
    }
  }
  return { directives, ignoreViolations };
}

function lexSource(source, isJsx = false) {
  const state = { out: "", comments: [], line: 1, idx: 0, lastToken: "", exprBraceStack: [] };
  while (state.idx < source.length) {
    const ch = source[state.idx];
    const next = source[state.idx + 1] ?? "";
    if (ch === "/" && next === "/") {
      consumeLineComment(source, state);
      continue;
    }
    if (ch === "/" && next === "*") {
      consumeBlockComment(source, state);
      continue;
    }
    if (ch === "'" && isJsx && /^[A-Za-z]$/.test(source[state.idx - 1] ?? "") && /^[A-Za-z]$/.test(next)) {
      advanceCodeChar(source, state, " ");
      continue;
    }
    if (ch === '"' || ch === "'") {
      consumeQuotedString(source, state, ch);
      continue;
    }
    if (ch === "`") {
      consumeTemplateLiteral(source, state);
      continue;
    }
    if (ch === "/" && canStartRegex(state.lastToken, isJsx)) {
      consumeRegexLiteral(source, state);
      continue;
    }
    advanceCodeChar(source, state, ch);
  }
  return { cleaned: state.out, comments: state.comments };
}

function consumeLineComment(source, state) {
  const start = state.idx;
  const startLine = state.line;
  while (state.idx < source.length && source[state.idx] !== "\n") {
    state.idx += 1;
  }
  state.comments.push({ line: startLine, text: source.slice(start, state.idx) });
}

function consumeBlockComment(source, state) {
  state.idx += 2;
  while (state.idx < source.length) {
    const ch = source[state.idx];
    if (ch === "*" && source[state.idx + 1] === "/") {
      state.idx += 2;
      return;
    }
    if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
    }
    state.idx += 1;
  }
}

function consumeQuotedString(source, state, quoteChar) {
  state.out += "_";
  state.idx += 1;
  while (state.idx < source.length) {
    const ch = source[state.idx];
    if (ch === "\\" && source[state.idx + 1] === "\n") {
      state.out += "\n";
      state.line += 1;
      state.idx += 2;
      continue;
    }
    if (ch === "\\") {
      state.idx += 2;
      continue;
    }
    if (ch === quoteChar) {
      state.idx += 1;
      state.lastToken = "literal";
      return;
    }
    if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
    }
    state.idx += 1;
  }
}

function consumeTemplateLiteral(source, state) {
  state.out += " ";
  state.idx += 1;
  while (state.idx < source.length) {
    const ch = source[state.idx];
    if (ch === "\\" && source[state.idx + 1] === "\n") {
      state.out += "\n";
      state.line += 1;
      state.idx += 2;
      continue;
    }
    if (ch === "\\") {
      state.idx += 2;
      continue;
    }
    if (ch === "$" && source[state.idx + 1] === "{") {
      state.exprBraceStack.push(0);
      state.out += "  ";
      state.idx += 2;
      state.lastToken = "{";
      return;
    }
    if (ch === "`") {
      state.idx += 1;
      state.lastToken = "literal";
      return;
    }
    if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
    }
    state.idx += 1;
  }
}

function consumeRegexLiteral(source, state) {
  state.out += " ";
  state.idx += 1;
  let inClass = false;
  while (state.idx < source.length) {
    const ch = source[state.idx];
    if (ch === "\\") {
      state.idx += 2;
      continue;
    }
    if (ch === "[") {
      inClass = true;
      state.idx += 1;
      continue;
    }
    if (ch === "]") {
      inClass = false;
      state.idx += 1;
      continue;
    }
    if (ch === "/" && !inClass) {
      state.idx += 1;
      const tailMatch = /^[a-z]+/i.exec(source.slice(state.idx));
      state.idx += tailMatch ? tailMatch[0].length : 0;
      state.lastToken = "literal";
      return;
    }
    if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
      state.idx += 1;
      return;
    }
    state.idx += 1;
  }
}

function advanceCodeChar(source, state, ch) {
  if (ch === "{" && state.exprBraceStack.length > 0) {
    state.exprBraceStack[state.exprBraceStack.length - 1] += 1;
  }
  if (ch === "}" && state.exprBraceStack.length > 0) {
    const topDepth = state.exprBraceStack[state.exprBraceStack.length - 1];
    if (topDepth === 0) {
      state.exprBraceStack.pop();
      state.out += ",";
      consumeTemplateLiteral(source, state);
      return;
    }
    state.exprBraceStack[state.exprBraceStack.length - 1] = topDepth - 1;
  }
  if (ch === "\n") {
    state.line += 1;
  }
  if (!/\s/.test(ch)) {
    state.lastToken = readWordOrPunct(source, state.idx, ch);
  }
  state.out += ch;
  state.idx += 1;
}

function readWordOrPunct(source, idx, ch) {
  if (!/[A-Za-z0-9_$]/.test(ch)) {
    return ch;
  }
  let start = idx;
  while (start > 0 && /[A-Za-z0-9_$]/.test(source[start - 1])) {
    start -= 1;
  }
  return source.slice(start, idx + 1);
}

function canStartRegex(lastToken, isJsx = false) {
  if (isJsx && lastToken === ">") {
    return false;
  }
  return REGEX_PRECEDING_CHARS.has(lastToken) || REGEX_PRECEDING_KEYWORDS.has(lastToken);
}

function extractFunctions(lines) {
  const spans = [];
  let braceDepth = 0;
  let parenDepth = 0;
  let pendingStart = -1;
  let activeStart = -1;
  let activeBodyStart = -1;
  let activeBaseDepth = 0;
  for (let idx = 0; idx < lines.length; idx += 1) {
    const trimmed = lines[idx].trim();
    const opens = countChar(lines[idx], "{");
    const closes = countChar(lines[idx], "}");
    const openParens = countChar(lines[idx], "(");
    const closeParens = countChar(lines[idx], ")");
    const isDeclStart = FUNC_DECL_PATTERN.test(trimmed) || ARROW_DECL_PATTERN.test(trimmed);
    if (activeStart === -1 && braceDepth === 0 && isDeclStart) {
      parenDepth = 0;
      pendingStart = idx + 1;
    } else if (activeStart === -1 && pendingStart === -1) {
      parenDepth = 0;
    }
    parenDepth += openParens - closeParens;
    const opensBodyHere = pendingStart !== -1 && parenDepth <= 0 && opens > 0 && (opens > closes || trimmed.endsWith("{"));
    const isSingleLineBody = pendingStart !== -1 && parenDepth <= 0 && opens > 0 && opens === closes && !trimmed.endsWith("{");
    if (isSingleLineBody) {
      spans.push({ startLine: pendingStart, bodyStartLine: idx + 1, endLine: idx + 1 });
      pendingStart = -1;
    } else if (opensBodyHere) {
      activeStart = pendingStart;
      activeBodyStart = idx + 1;
      activeBaseDepth = braceDepth - (trimmed.startsWith("}") ? 1 : 0);
      pendingStart = -1;
    } else if (pendingStart !== -1 && parenDepth <= 0 && (trimmed.endsWith(";") || (opens === 0 && /=>\s*[^({\s]/.test(trimmed)))) {
      pendingStart = -1;
    }
    braceDepth += opens - closes;
    if (activeStart !== -1 && braceDepth <= activeBaseDepth) {
      spans.push({ startLine: activeStart, bodyStartLine: activeBodyStart, endLine: idx + 1 });
      activeStart = -1;
    }
  }
  return spans;
}

function measureControlNesting(lines, bodyStartLine, endLine) {
  const controlStack = [];
  let braceDepth = 0;
  let maxNesting = 0;
  for (let lineNo = bodyStartLine; lineNo <= endLine; lineNo += 1) {
    const lineText = lines[lineNo - 1];
    const trimmed = lineText.trim();
    const leadingCloses = countLeadingCloses(trimmed);
    const opens = countChar(lineText, "{");
    const closes = countChar(lineText, "}");
    braceDepth -= leadingCloses;
    while (controlStack.length > 0 && braceDepth <= controlStack[controlStack.length - 1]) {
      controlStack.pop();
    }
    if (lineNo > bodyStartLine && CONTROL_OPEN_PATTERN.test(lineText)) {
      controlStack.push(braceDepth);
      maxNesting = Math.max(maxNesting, controlStack.length);
    }
    braceDepth += opens - (closes - leadingCloses);
    while (controlStack.length > 0 && braceDepth <= controlStack[controlStack.length - 1]) {
      controlStack.pop();
    }
  }
  return maxNesting;
}

function countLeadingCloses(trimmedLine) {
  let count = 0;
  while (count < trimmedLine.length && trimmedLine[count] === "}") {
    count += 1;
  }
  return count;
}

function findNestedFunctions(lines, bodyStartLine, endLine, filePath) {
  const violations = [];
  for (let lineNo = bodyStartLine + 1; lineNo <= endLine; lineNo += 1) {
    if (/\bfunction\s+[A-Za-z_$]/.test(lines[lineNo - 1])) {
      violations.push({
        path: filePath,
        line: lineNo,
        rule: "CANON-05",
        message: "Nested function declaration inside a function is banned.",
      });
    }
  }
  return violations;
}

function filterSuppressed(violations, functions, directives) {
  return violations.filter((entry) => {
    const candidateLines = new Set([entry.line, entry.line - 1]);
    for (const fnSpan of functions) {
      if (fnSpan.startLine <= entry.line && entry.line <= fnSpan.endLine) {
        candidateLines.add(fnSpan.startLine);
        candidateLines.add(fnSpan.startLine - 1);
      }
    }
    for (const lineNo of candidateLines) {
      const directive = directives.get(lineNo);
      if (directive && directive.hasReason && (directive.isWildcard || directive.rules.has(entry.rule))) {
        return false;
      }
    }
    return true;
  });
}

function hasNestedTernary(lineText) {
  const sanitized = lineText
    .replace(/\?\./g, "")
    .replace(/\?\?/g, "")
    .replace(/[\w}\]]\?\s*:/g, "")
    .replace(/\w\?\s*(?=[,)\]])/g, "");
  const scopes = [{ active: false, childHad: false }];
  for (const ch of sanitized) {
    if (ch === "(" || ch === "{" || ch === "[") {
      scopes.push({ active: false, childHad: false });
    } else if ((ch === ")" || ch === "}" || ch === "]") && scopes.length > 1) {
      const closed = scopes.pop();
      scopes[scopes.length - 1].childHad ||= closed.active || closed.childHad;
    } else if (ch === "," || ch === ";") {
      scopes[scopes.length - 1] = { active: false, childHad: false };
    } else if (ch === "?" && (scopes.some((s) => s.active) || scopes[scopes.length - 1].childHad)) {
      return true;
    } else if (ch === "?") {
      scopes[scopes.length - 1].active = true;
    }
  }
  return false;
}

function countChar(text, target) {
  let count = 0;
  for (let idx = 0; idx < text.length; idx += 1) {
    if (text[idx] === target) {
      count += 1;
    }
  }
  return count;
}

function collectTopLevelAstFunctions(sf, tsMod) {
  const functions = [];
  for (const stmt of sf.statements) {
    if (!tsMod.isFunctionDeclaration(stmt) || !stmt.name || !stmt.body) {
      continue;
    }
    const startLine = sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1;
    const endLine = sf.getLineAndCharacterOfPosition(stmt.getEnd()).line + 1;
    functions.push({ name: stmt.name.text, node: stmt, startLine, endLine });
  }
  return functions;
}

function collectAstMemberFunctions(sf, tsMod) {
  const members = [];
  for (const stmt of sf.statements) {
    const decls = tsMod.isVariableStatement(stmt)
      ? stmt.declarationList.declarations.filter(
          (decl) => tsMod.isIdentifier(decl.name) && decl.initializer && tsMod.isArrowFunction(decl.initializer) && tsMod.isBlock(decl.initializer.body),
        )
      : [];
    members.push(
      ...decls.map((decl) => ({
        name: decl.name.text,
        node: decl.initializer,
        startLine: sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1,
        endLine: sf.getLineAndCharacterOfPosition(decl.initializer.getEnd()).line + 1,
      })),
    );
    const methods = tsMod.isClassDeclaration(stmt)
      ? stmt.members.filter((member) => tsMod.isMethodDeclaration(member) && member.body && member.name)
      : [];
    members.push(
      ...methods.map((member) => ({
        name: member.name.getText(sf),
        node: member,
        startLine: sf.getLineAndCharacterOfPosition(member.getStart(sf)).line + 1,
        endLine: sf.getLineAndCharacterOfPosition(member.getEnd()).line + 1,
      })),
    );
  }
  return members;
}

function checkAstOrderAndDepth(topFunctions, sf, tsMod, filePath) {
  const violations = [];
  const nameToLine = new Map(topFunctions.map((entry) => [entry.name, entry.startLine]));
  const nameSet = new Set(nameToLine.keys());
  const callGraph = new Map();
  for (const entry of topFunctions) {
    const callees = collectAstCalls(entry.node, nameSet, tsMod);
    callGraph.set(entry.name, callees);
    const outOfOrder = [...callees]
      .sort()
      .filter((callee) => callee !== entry.name && nameToLine.get(callee) < entry.startLine);
    for (const callee of outOfOrder) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' calls '${callee}' defined above it -> move '${callee}' below '${entry.name}' for top-down order.`,
      });
    }
  }
  for (const entry of topFunctions) {
    const { depth, leafCallsLocal } = computeAstChainDepth(entry.name, callGraph);
    if (depth > MAX_BUSINESS_DEPTH + 1 || (depth === MAX_BUSINESS_DEPTH + 1 && leafCallsLocal)) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' reaches call depth ${depth} (max L0->L1->L2 + L3 leaf) -> flatten pass-through calls.`,
      });
    }
  }
  return violations;
}

function checkAstFunctionRules(topFunctions, sf, tsMod, filePath) {
  const violations = [];
  for (const entry of topFunctions) {
    const length = entry.endLine - entry.startLine + 1;
    if (length > MAX_FUNCTION_LINES) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' is ${length} lines (max ${MAX_FUNCTION_LINES}) -> extract a cohesive L1/L2 pure helper.`,
      });
    }
    if (GAMING_NAME_PATTERN.test(entry.name)) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "LLM-05",
        message: `Function '${entry.name}' looks like a rule-gaming split -> rename by domain responsibility.`,
      });
    }
    if (entry.node.parameters.length > MAX_POSITIONAL_ARGS) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-03",
        message: `'${entry.name}' has ${entry.node.parameters.length} params (max ${MAX_POSITIONAL_ARGS}) -> group extras into a destructured options object.`,
      });
    }
    violations.push(...scanAstFunctionBody(entry, sf, tsMod, filePath));
  }
  return violations;
}

function scanAstFunctionBody(entry, sf, tsMod, filePath) {
  const violations = [];
  let maxNesting = 0;
  const stack = [{ node: entry.node.body, depth: 0 }];
  while (stack.length > 0) {
    const { node, depth } = stack.pop();
    const isControl =
      tsMod.isIfStatement(node) ||
      tsMod.isForStatement(node) ||
      tsMod.isForInStatement(node) ||
      tsMod.isForOfStatement(node) ||
      tsMod.isWhileStatement(node) ||
      tsMod.isSwitchStatement(node) ||
      tsMod.isTryStatement(node);
    const nextDepth = isControl ? depth + 1 : depth;
    maxNesting = Math.max(maxNesting, nextDepth);
    const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
    if (tsMod.isFunctionDeclaration(node) && node !== entry.node) {
      violations.push({ path: filePath, line, rule: "CANON-05", message: "Nested function declaration is banned -> lift to module top level." });
    } else if (tsMod.isConditionalExpression(node) && (isConditionalExpr(node.whenTrue, tsMod) || isConditionalExpr(node.whenFalse, tsMod))) {
      violations.push({ path: filePath, line, rule: "CANON-05", message: "Nested ternary expression is banned -> replace with flat if/return guard clauses." });
    } else if (tsMod.isCatchClause(node) && node.block.statements.length === 0) {
      violations.push({ path: filePath, line, rule: "LLM-02", message: "Empty catch block silently swallows errors -> handle or propagate to L0." });
    } else if (tsMod.isVariableDeclaration(node) && tsMod.isIdentifier(node.name) && (VAGUE_NOUNS.has(node.name.text) || (node.name.text.length === 1 && node.name.text !== "_"))) {
      violations.push({ path: filePath, line, rule: "LLM-03", message: `Vague or single-char variable '${node.name.text}' -> use a descriptive domain noun.` });
    }
    node.forEachChild((child) => {
      const childDepth = tsMod.isIfStatement(node) && child === node.elseStatement && tsMod.isIfStatement(child) ? depth : nextDepth;
      stack.push({ node: child, depth: childDepth });
    });
  }
  if (maxNesting > MAX_NESTING_DEPTH) {
    violations.push({
      path: filePath,
      line: entry.startLine,
      rule: "CANON-05",
      message: `Control-flow nesting depth ${maxNesting} (max ${MAX_NESTING_DEPTH}) -> invert with early return/continue or extract an L2 predicate.`,
    });
  }
  return violations;
}

function isConditionalExpr(node, tsMod) {
  let current = node;
  while (tsMod.isParenthesizedExpression(current)) {
    current = current.expression;
  }
  return tsMod.isConditionalExpression(current);
}

function collectAstCalls(fnNode, nameSet, tsMod) {
  const callees = new Set();
  const stack = [fnNode.body];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) {
      continue;
    }
    if (tsMod.isCallExpression(current) && tsMod.isIdentifier(current.expression) && nameSet.has(current.expression.text)) {
      callees.add(current.expression.text);
    }
    current.forEachChild((child) => stack.push(child));
  }
  return callees;
}

function computeAstChainDepth(startName, callGraph) {
  let maxDepth = 0;
  let leafCallsLocal = false;
  const stack = [{ name: startName, depth: 0, visited: new Set([startName]) }];
  while (stack.length > 0) {
    const { name, depth, visited } = stack.pop();
    const next = callGraph.get(name) ?? new Set();
    if (depth > maxDepth) {
      maxDepth = depth;
      leafCallsLocal = next.size > 0;
    } else if (depth === maxDepth && next.size > 0) {
      leafCallsLocal = true;
    }
    const unvisited = [...next].filter((callee) => !visited.has(callee));
    for (const callee of unvisited) {
      const nextVisited = new Set(visited);
      nextVisited.add(callee);
      stack.push({ name: callee, depth: depth + 1, visited: nextVisited });
    }
  }
  return { depth: maxDepth, leafCallsLocal };
}

function topoSortAstFunctions(fnPositions, callGraph) {
  const nameToIdx = new Map(fnPositions.map((entry, idx) => [entry.name, idx]));
  const inDegree = new Array(fnPositions.length).fill(0);
  const edges = fnPositions.map(() => []);
  for (let callerIdx = 0; callerIdx < fnPositions.length; callerIdx += 1) {
    const calleeIndices = [...(callGraph.get(fnPositions[callerIdx].name) ?? [])]
      .sort()
      .map((calleeName) => nameToIdx.get(calleeName))
      .filter((calleeIdx) => calleeIdx !== undefined && calleeIdx !== callerIdx);
    for (const calleeIdx of calleeIndices) {
      edges[callerIdx].push(calleeIdx);
      inDegree[calleeIdx] += 1;
    }
  }
  const ready = inDegree.map((deg, idx) => (deg === 0 ? idx : -1)).filter((idx) => idx >= 0);
  const ordered = [];
  while (ready.length > 0) {
    ready.sort((left, right) => left - right);
    const current = ready.shift();
    ordered.push(current);
    for (const neighbor of edges[current]) {
      inDegree[neighbor] -= 1;
    }
    ready.push(...edges[current].filter((neighbor) => inDegree[neighbor] === 0));
  }
  return ordered.length === fnPositions.length ? ordered : null;
}

const isEntryPoint =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isEntryPoint) {
  main().then((code) => process.exit(code));
}
