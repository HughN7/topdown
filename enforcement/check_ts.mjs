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
const IGNORE_PATTERN = /\/\/\s*topdown-ignore(?:\((.*)\))?/;
const REASON_PATTERN = /reason\s*=\s*(?:"([^"]+)"|'([^']+)')/;
const RULE_ID_PATTERN = /\b(?:CANON|LLM)-\d{2}\b/g;
const FUNC_DECL_PATTERN = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b/;
const ARROW_DECL_PATTERN = /^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$]\w*\s*=\s*(?:async\s*)?\(/;
const CONTROL_OPEN_PATTERN = /\b(?:if|for|while|switch|try|catch)\b[^{]*\{|\belse\s*\{/;
const EMPTY_CATCH_FULL_PATTERN = /\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}/g;
const TEST_RUNNER_BRANCH_PATTERN =
  /\bprocess\.env\.(?:VITEST|JEST_WORKER_ID)\b|\bprocess\.env\.NODE_ENV\s*===?\s*["']test["']/;
const TEST_PATH_PATTERN =
  /(?:^|[\\/])(?:tests?|__tests__)[\\/]|\.(?:test|spec)\.[a-z]+$/i;
const REGEX_PRECEDING_CHARS = new Set(["", "(", "[", "{", ",", ":", ";", "=", "!", "&", "|", "?", "+", "-", "*", "%", "~", "^", "\n"]);
const REGEX_PRECEDING_KEYWORDS = new Set(["return", "case", "throw", "in", "of", "typeof", "void", "delete", "new", "yield", "await"]);

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
  const { comments } = lexSource(source);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const sf = tsMod.createSourceFile(filePath, source, tsMod.ScriptTarget.Latest, true);
  const topFunctions = collectTopLevelAstFunctions(sf, tsMod);
  const rawViolations = [
    ...checkAstOrderAndDepth(topFunctions, sf, tsMod, filePath),
    ...checkAstFunctionRules(topFunctions, sf, tsMod, filePath),
    ...checkTestRunnerBranching(source, filePath),
  ];
  const spans = topFunctions.map((entry) => ({ startLine: entry.startLine, endLine: entry.endLine }));
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
  const prefix = source.slice(0, fnPositions[0].stmt.getFullStart());
  const suffix = source.slice(fnPositions[fnPositions.length - 1].stmt.getEnd());
  const blocks = ordered.map((idx) => source.slice(fnPositions[idx].stmt.getFullStart(), fnPositions[idx].stmt.getEnd()).trim());
  const prefixTrim = prefix.trimEnd();
  const lead = prefixTrim ? `${prefixTrim}\n\n` : "";
  return { source: `${lead}${blocks.join("\n\n")}${suffix || "\n"}`, refusal: null };
}

export function checkSource(source, filePath) {
  const { cleaned, comments } = lexSource(source);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const lines = cleaned.split(/\r?\n/);
  const functions = extractFunctions(lines);
  const rawViolations = [
    ...checkFunctionBlocks(functions, lines, filePath),
    ...checkTernariesAndEmptyCatches(cleaned, lines, filePath),
    ...checkTestRunnerBranching(source, filePath),
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

function checkTestRunnerBranching(source, filePath) {
  if (TEST_PATH_PATTERN.test(filePath)) {
    return [];
  }
  const violations = [];
  const rawLines = source.split(/\r?\n/);
  for (let idx = 0; idx < rawLines.length; idx += 1) {
    if (TEST_RUNNER_BRANCH_PATTERN.test(rawLines[idx]) && !rawLines[idx].trim().startsWith("//")) {
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
    } else if (token === "--fix") {
      applyFix = true;
    } else if (token === "--dry-run") {
      dryRun = true;
    } else if (token === "--show-backend") {
      showBackend = true;
    } else if (token.startsWith("--backend=")) {
      const value = token.slice("--backend=".length);
      if (!["auto", "ast", "heuristic"].includes(value)) {
        return null;
      }
      backend = value;
    } else if (token.startsWith("-")) {
      return null;
    } else {
      paths.push(token);
    }
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
        .filter((entry) => entry.isDirectory() && entry.name !== "node_modules")
        .map((entry) => path.join(current, entry.name)),
    );
    files.push(
      ...entries
        .filter((entry) => entry.isFile() && TS_EXTENSIONS.has(path.extname(entry.name)))
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
    directives.set(comment.line, { line: comment.line, rules, hasReason });
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

function lexSource(source) {
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
    if (ch === '"' || ch === "'") {
      consumeQuotedString(source, state, ch);
      continue;
    }
    if (ch === "`") {
      consumeTemplateLiteral(source, state);
      continue;
    }
    if (ch === "/" && canStartRegex(state.lastToken)) {
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
    } else if (ch === "]") {
      inClass = false;
    } else if (ch === "/" && !inClass) {
      state.idx += 1;
      while (state.idx < source.length && /[a-z]/i.test(source[state.idx])) {
        state.idx += 1;
      }
      state.lastToken = "literal";
      return;
    } else if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
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

function canStartRegex(lastToken) {
  return REGEX_PRECEDING_CHARS.has(lastToken) || REGEX_PRECEDING_KEYWORDS.has(lastToken);
}

function extractFunctions(lines) {
  const spans = [];
  let braceDepth = 0;
  let pendingStart = -1;
  let activeStart = -1;
  let activeBodyStart = -1;
  let activeBaseDepth = 0;
  for (let idx = 0; idx < lines.length; idx += 1) {
    const trimmed = lines[idx].trim();
    const opens = countChar(lines[idx], "{");
    const closes = countChar(lines[idx], "}");
    const isDeclStart = FUNC_DECL_PATTERN.test(trimmed) || ARROW_DECL_PATTERN.test(trimmed);
    if (activeStart === -1 && pendingStart === -1 && braceDepth === 0 && isDeclStart) {
      pendingStart = idx + 1;
    }
    if (pendingStart !== -1 && opens > 0) {
      activeStart = pendingStart;
      activeBodyStart = idx + 1;
      activeBaseDepth = braceDepth;
      pendingStart = -1;
    } else if (pendingStart !== -1 && trimmed.endsWith(";")) {
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
    const isElseIf = /\belse\s+if\b/.test(lineText);
    const leadingCloses = countLeadingCloses(trimmed);
    const opens = countChar(lineText, "{");
    const closes = countChar(lineText, "}");
    braceDepth -= leadingCloses;
    while (controlStack.length > 0 && braceDepth <= controlStack[controlStack.length - 1]) {
      controlStack.pop();
    }
    if (lineNo > bodyStartLine && !isElseIf && CONTROL_OPEN_PATTERN.test(lineText)) {
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
      if (directive && directive.hasReason && (directive.rules.size === 0 || directive.rules.has(entry.rule))) {
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
    .replace(/\w\?\s*:/g, "")
    .replace(/\w\?\s*(?=[,)\]])/g, "");
  return sanitized.split(",").some((segment) => countChar(segment, "?") >= 2);
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
    } else if (tsMod.isConditionalExpression(node) && (tsMod.isConditionalExpression(node.whenTrue) || tsMod.isConditionalExpression(node.whenFalse))) {
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
