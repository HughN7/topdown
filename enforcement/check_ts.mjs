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
const TS_EXTENSIONS = new Set([".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]);
const IGNORE_PATTERN = /\/\/\s*topdown-ignore(?:\((.*)\))?/;
const REASON_PATTERN = /reason\s*=\s*(?:"([^"]+)"|'([^']+)')/;
const RULE_ID_PATTERN = /\b(?:CANON|LLM)-\d{2}\b/g;
const FUNC_DECL_PATTERN = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b/;
const ARROW_DECL_PATTERN = /^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$]\w*\s*=\s*(?:async\s*)?\(/;
const CONTROL_OPEN_PATTERN = /\b(?:if|for|while|switch|try|catch)\b[^{]*\{|\belse\s*\{/;
const EMPTY_CATCH_FULL_PATTERN = /\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}/g;
const REGEX_PRECEDING_CHARS = new Set(["", "(", "[", "{", ",", ":", ";", "=", "!", "&", "|", "?", "+", "-", "*", "%", "~", "^", "\n"]);
const REGEX_PRECEDING_KEYWORDS = new Set(["return", "case", "throw", "in", "of", "typeof", "void", "delete", "new", "yield", "await"]);

function main() {
  const options = parseCliArgs(process.argv.slice(2));
  if (!options) {
    process.stderr.write("Usage: node check_ts.mjs [--json] <path> [...]\n");
    return 2;
  }
  const collected = collectTargetFiles(options.paths);
  if (collected.error) {
    process.stderr.write(`${collected.error}\n`);
    return 2;
  }
  const allViolations = [];
  for (const filePath of collected.files) {
    try {
      const source = fs.readFileSync(filePath, "utf8");
      allViolations.push(...checkSource(source, filePath));
    } catch (err) {
      process.stderr.write(`Error reading ${filePath}: ${err.message}\n`);
      return 2;
    }
  }
  emitViolations(allViolations, options.asJson);
  return allViolations.length > 0 ? 1 : 0;
}

export function checkSource(source, filePath) {
  const { cleaned, comments } = lexSource(source);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const lines = cleaned.split(/\r?\n/);
  const functions = extractFunctions(lines);
  const rawViolations = [
    ...checkFunctionBlocks(functions, lines, filePath),
    ...checkTernariesAndEmptyCatches(cleaned, lines, filePath),
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

function parseCliArgs(rawArgs) {
  let asJson = false;
  const paths = [];
  for (const token of rawArgs) {
    if (token === "--json") {
      asJson = true;
      continue;
    }
    if (token.startsWith("-")) {
      return null;
    }
    paths.push(token);
  }
  return paths.length > 0 ? { paths, asJson } : null;
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

const isEntryPoint =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isEntryPoint) {
  process.exit(main());
}
