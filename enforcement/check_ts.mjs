/**
 * Lightweight zero-dependency heuristic TS/JS structural checker for topdown.
 * Intended as a complement to ESLint (see languages/typescript.md), not a full AST parser.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath, pathToFileURL } from "node:url";

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
const TIER_TAG_PATTERN = /^\s*\/\/\s*\[L([0-3])\](?:\s|$)/;
const TS_HEADER_COMMENT_PATTERN = /^(?:\/\/|\/\*+)\s*(?:@ts-|eslint-disable|copyright\b|license\b|spdx-license-identifier\b)/i;
const FUNC_DECL_PATTERN = /^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\b/;
const ARROW_DECL_PATTERN =
  /^(?:export\s+)?(?:const|let|var)\s+[A-Za-z_$]\w*\s*=\s*(?:async\s*)?(?:<[^>]*>\s*)?\(/;
const FN_EVENT_PATTERN = /=>|[{};\[\]()]/g;
const TS_CONTROL_WORDS = new Set(["if", "else", "for", "while", "switch", "try", "catch"]);
const TS_NESTING_TOKEN_PATTERN =
  /\/\/.*|(?<!\.)\b(?:if|else|for|while|switch|try|catch|return|throw|break|continue)\b(?!\s*:)|[{}\(\)\[\];]/g;
const TS_HEADER_PAIR_MAP = { "]": "[", ")": "(", "}": "{" };
const TS_HEADER_NOISE_PATTERN =
  /"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|\/\*[\s\S]*?\*\/|\/\/.*/g;
const PAREN_DELTAS = { "(": 1, ")": -1 };
const BRACKET_DELTAS = { "[": 1, "]": -1 };
const EMPTY_CATCH_FULL_PATTERN = /\bcatch\s*(?:\([^)]*\))?\s*\{\s*\}/g;
const TEST_RUNNER_BRANCH_PATTERN =
  /\bprocess\.env\.(?:VITEST|JEST_WORKER_ID)\b|\bprocess\.env\.NODE_ENV\s*===?\s*["']test["']/;
const TEST_PATH_PATTERN =
  /(?:^|[\\/])(?:tests?|__tests__)[\\/]|\.(?:test|spec)\.[a-z]+$/i;
const LIFECYCLE_BOOL_NAME =
  "(?:(?:is|has|was)(?:Loading|Pending|Fetching|Submitting|Saving|Retrying|Error|Errored|Failed|Rejected|Success|Succeeded|Loaded|Ready|Done|Completed|Idle|_(?:loading|pending|fetching|submitting|saving|retrying|error|errored|failed|rejected|success|succeeded|loaded|ready|done|completed|idle))|(?:loading|pending|fetching|submitting|errored|failed|succeeded))";
const LIFECYCLE_BOOL_DECL_PATTERN = new RegExp(
  `\\b(${LIFECYCLE_BOOL_NAME})\\b(?:\\s*\\??\\s*:\\s*boolean\\b|\\s*=\\s*(?:true|false|\\$state(?:\\s*<\\s*boolean\\s*>|\\s*\\(\\s*(?:true|false)\\s*\\))))|\\[\\s*(${LIFECYCLE_BOOL_NAME})\\s*,[^\\]]*\\]\\s*=\\s*(?:React\\.)?useState\\b`,
  "g",
);
const TS_STATE_MACHINE_TOKEN_PATTERN = new RegExp(
  `=>|(?<!\\.)\\b(?:if|else|for|while|switch|try|catch|return|throw|break|continue|const|let|var|function)\\b(?!\\s*:)|${LIFECYCLE_BOOL_DECL_PATTERN.source}|[{}\\[\\]()\\;]`,
  "g",
);
const REGEX_PRECEDING_CHARS = new Set(["", "(", "[", "{", ",", ":", ";", "=", "!", "&", "|", "?", "+", "-", "*", "%", "~", "^", ">", "\n"]);
const REGEX_PRECEDING_KEYWORDS = new Set(["return", "case", "default", "throw", "in", "of", "typeof", "void", "delete", "new", "yield", "await"]);

async function main() {
  const options = parseCliArgs(process.argv.slice(2));
  if (!options) {
    process.stderr.write(
      "Usage: node check_ts.mjs [--json] [--backend=auto|ast|heuristic] [--show-backend] [--fix] [--dry-run] [--outline] <path> [...]\n",
    );
    return 2;
  }
  const tsMod = options.backend === "heuristic" ? null : await loadTypeScript();
  if (options.backend === "ast" && !tsMod) {
    process.stderr.write("Error: --backend=ast requested, but 'typescript' package is not installed.\n");
    return 2;
  }
  if ((options.applyFix || options.outline) && !tsMod) {
    process.stderr.write("Error: --fix and --outline require the optional 'typescript' AST backend (or run 'topdown --outline' via engine).\n");
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
  if (options.outline) {
    return runTsOutline(collected.files, options.asJson, tsMod);
  }
  const allViolations = [];
  for (const filePath of collected.files) {
    const readResult = readFileText(filePath);
    if (readResult.error) {
      process.stderr.write(`${readResult.error}\n`);
      return 2;
    }
    const source =
      options.applyFix && tsMod
        ? applyAstFix({ source: readResult.source, filePath, dryRun: options.dryRun }, tsMod)
        : readResult.source;
    const checked = tsMod ? checkSourceWithAst(source, filePath, tsMod) : checkSource(source, filePath);
    const violations = options.applyFix && options.dryRun ? [] : checked;
    allViolations.push(...violations);
  }
  if (!options.dryRun) {
    emitViolations(allViolations, options.asJson);
  }
  return allViolations.length > 0 ? 1 : 0;
}

function runTsOutline(files, asJson, tsMod) {
  const payload = [];
  for (const filePath of files) {
    const readResult = readFileText(filePath);
    if (readResult.error) {
      process.stderr.write(`${readResult.error}\n`);
      return 2;
    }
    const isJsx = /\.[jt]sx$/i.test(filePath);
    const lexed = lexSource(readResult.source, isJsx);
    const sf = tsMod.createSourceFile(filePath, readResult.source, tsMod.ScriptTarget.Latest, true);
    const topFunctions = collectTopLevelAstFunctions(sf, tsMod);
    const rawLines = readResult.source.split("\n");
    const tierCtx = collectAstModuleTierContext(topFunctions, filePath, {
      rawLines,
      lineCommentLines: lexed.lineCommentLines,
      commentLines: lexed.commentLines,
      tsMod,
    });
    const functions = buildAstOutlineEntries(topFunctions, tierCtx);
    payload.push({ path: filePath, functions });
  }
  const output = asJson ? `${JSON.stringify(payload, null, 2)}\n` : `${formatOutlineText(payload)}\n`;
  process.stdout.write(output);
  return 0;
}

function applyAstFix({ source, filePath, dryRun }, tsMod) {
  const fixed = fixSourceWithAst(source, filePath, tsMod);
  if (fixed.refusal) {
    process.stderr.write(`${filePath}: ${fixed.refusal}\n`);
    return source;
  }
  if (fixed.source === source) {
    return source;
  }
  if (dryRun) {
    process.stdout.write(`--- a/${filePath}\n+++ b/${filePath}\n${fixed.source}`);
    return source;
  }
  fs.writeFileSync(filePath, fixed.source, "utf8");
  return fixed.source;
}

export async function loadTypeScript() {
  try {
    const loaded = await import("typescript");
    return loaded.default ?? loaded;
  } catch {
    if (!process.env.APPDATA) {
      return null;
    }
  }
  try {
    const globalTsPath = path.join(process.env.APPDATA, "npm", "node_modules", "typescript", "lib", "typescript.js");
    const loaded = await import(pathToFileURL(globalTsPath).href);
    return loaded.default ?? loaded;
  } catch {
    return null;
  }
}

export function buildAstFileOutline(source, filePath, tsMod) {
  const isJsx = /\.[jt]sx$/i.test(filePath);
  const lexed = lexSource(source, isJsx);
  const sf = tsMod.createSourceFile(filePath, source, tsMod.ScriptTarget.Latest, true);
  const topFunctions = collectTopLevelAstFunctions(sf, tsMod);
  const rawLines = source.split("\n");
  const tierCtx = collectAstModuleTierContext(topFunctions, filePath, {
    rawLines,
    lineCommentLines: lexed.lineCommentLines,
    commentLines: lexed.commentLines,
    tsMod,
  });
  return buildAstOutlineEntries(topFunctions, tierCtx);
}

export function checkSourceWithAst(source, filePath, tsMod) {
  const isJsx = /\.[jt]sx$/i.test(filePath);
  const { cleaned, comments, commentLines, lineCommentLines } = lexSource(source, isJsx);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const sf = tsMod.createSourceFile(filePath, source, tsMod.ScriptTarget.Latest, true);
  const topFunctions = collectTopLevelAstFunctions(sf, tsMod);
  const memberFunctions = collectAstMemberFunctions(sf, tsMod);
  const allFunctions = [...topFunctions, ...memberFunctions];
  const rawLines = source.split("\n");
  const cleanedLines = cleaned.split("\n");
  const tierCtx = collectAstModuleTierContext(topFunctions, filePath, {
    rawLines,
    lineCommentLines,
    commentLines,
    tsMod,
  });
  const rawViolations = [
    ...tierCtx.multiTagViolations,
    ...checkAstOrderAndDepth(topFunctions, filePath, tsMod),
    ...checkAstTierTags(topFunctions, filePath, tierCtx),
    ...checkAstDuplicateLeaves(topFunctions, {
      filePath,
      rawLines,
      commentLines,
      callGraph: tierCtx.callGraph,
      declaredTags: tierCtx.declaredTags,
      sf,
      tsMod,
    }),
    ...checkAstFunctionRules(allFunctions, { sf, tsMod, filePath }),
    ...checkImplicitStateMachines(cleanedLines, filePath, { directives, rawLines, commentLines }),
    ...checkTestRunnerBranching(source, cleanedLines, filePath),
  ];
  const spans = allFunctions.map((entry) => ({
    startLine: entry.startLine,
    outerStartLine: entry.outerStartLine ?? entry.startLine,
    endLine: entry.endLine,
  }));
  const kept = filterSuppressed(rawViolations, spans, { directives, rawLines, commentLines });
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
  if (new Set(fnPositions.map((entry) => entry.name)).size !== fnPositions.length) {
    return { source, refusal: "Refused --fix: duplicate top-level function names detected." };
  }
  const firstPos = fnPositions[0].idx;
  const lastPos = fnPositions[fnPositions.length - 1].idx;
  if (lastPos - firstPos + 1 !== fnPositions.length) {
    return { source, refusal: "Refused --fix: interleaved statements or non-hoisted declarations between top-level functions." };
  }
  const keysByName = new Map(fnPositions.map((entry) => [entry.name, [entry.name]]));
  const callGraph = new Map(fnPositions.map((entry) => [entry.name, collectAstCalls(entry.stmt, keysByName, tsMod)]));
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
  const { cleaned, comments, commentLines } = lexSource(source, isJsx);
  const { directives, ignoreViolations } = parseIgnoreDirectives(comments, filePath);
  const lines = cleaned.split("\n");
  const rawLines = source.split("\n");
  const functions = extractFunctions(lines);
  const rawViolations = [
    ...checkFunctionBlocks(functions, lines, filePath),
    ...checkTernariesAndEmptyCatches(cleaned, lines, filePath),
    ...checkImplicitStateMachines(lines, filePath, { directives, rawLines, commentLines }),
    ...checkTestRunnerBranching(source, lines, filePath),
  ];
  const kept = filterSuppressed(rawViolations, functions, { directives, rawLines, commentLines });
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
    const maxNesting = measureControlNesting(lines, fnSpan);
    if (maxNesting > MAX_NESTING_DEPTH) {
      violations.push({
        path: filePath,
        line: fnSpan.startLine,
        rule: "CANON-05",
        message: `Control-flow nesting depth ${maxNesting} (max ${MAX_NESTING_DEPTH}) -> invert with early return/continue or extract an L2 predicate.`,
      });
    }
    violations.push(...findNestedFunctions(lines, fnSpan, filePath));
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

function checkImplicitStateMachines(lines, filePath, { directives, rawLines, commentLines }) {
  const violations = [];
  const state = {
    scopeStack: [],
    closedScopes: [],
    pendingFlags: [],
    pendingControls: [],
    parenDepth: 0,
    bracketDepth: 0,
    braceDepth: 0,
    pendingStart: -1,
    lastClosedEnd: 0,
    declKind: "",
    seenArrow: false,
  };
  for (let idx = 0; idx < lines.length; idx += 1) {
    advanceStateMachineLine(lines[idx], idx + 1, state);
  }
  for (const scope of state.closedScopes) {
    const uniqueFlags = [...new Set(scope.flags)];
    const leading = collectContiguousHeaderLines(rawLines, scope.startLine, {
      prevEndLine: scope.prevEndLine ?? 0,
      commentLines,
    });
    const headerLines = [
      ...leading,
      ...Array.from({ length: scope.openLine - scope.startLine + 1 }, (_, offset) => scope.startLine + offset),
    ];
    const isSuppressed = headerLines.some((lineNo) => {
      const directive = directives.get(lineNo);
      return Boolean(directive && directive.hasReason && (directive.isWildcard || directive.rules.has("CANON-03")));
    });
    if (uniqueFlags.length >= 2 && !isSuppressed) {
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

function advanceStateMachineLine(lineText, lineNo, state) {
  let cursor = 0;
  for (const match of lineText.matchAll(TS_STATE_MACHINE_TOKEN_PATTERN)) {
    const pos = match.index;
    const tok = match[0];
    const boolName = match[1] ?? match[2] ?? "";
    if (state.pendingStart === -1 && lineText.slice(cursor, pos).trim().length > 0) { state.pendingStart = lineNo; }
    cursor = pos + tok.length;
    if (tok !== "}" && tok !== ";" && state.pendingStart === -1 && tok.trim().length > 0) { state.pendingStart = lineNo; }
    const top = state.pendingControls.length > 0 ? state.pendingControls[state.pendingControls.length - 1] : null;
    const isTopDepth = top !== null && top[0] === state.parenDepth && top[1] === state.bracketDepth && top[2] === state.braceDepth;
    if (tok === "(" || tok === ")" || tok === "[" || tok === "]") {
      state.parenDepth = Math.max(0, state.parenDepth + (PAREN_DELTAS[tok] ?? 0));
      state.bracketDepth = Math.max(0, state.bracketDepth + (BRACKET_DELTAS[tok] ?? 0));
    } else if (tok === "=>") {
      state.seenArrow = state.parenDepth === 0 && state.bracketDepth === 0 ? true : state.seenArrow;
    } else if (tok === "const" || tok === "let" || tok === "var" || tok === "function") {
      const resetPrev = state.scopeStack.length === 0 && state.parenDepth === 0 && state.bracketDepth === 0 && state.declKind !== "";
      state.pendingFlags = resetPrev ? [] : state.pendingFlags;
      state.pendingStart = resetPrev ? lineNo : state.pendingStart;
      state.declKind = tok === "function" ? "fn" : "value";
      state.seenArrow = false;
    } else if (tok === ";" && (state.parenDepth > 0 || state.bracketDepth > 0)) {
      continue;
    } else if (tok === ";") {
      (state.scopeStack.length > 0 ? state.scopeStack[state.scopeStack.length - 1].flags : null)?.push(...state.pendingFlags);
      state.pendingFlags = [];
      state.pendingStart = -1;
      state.declKind = "";
      state.seenArrow = false;
      state.pendingControls = isTopDepth ? state.pendingControls.slice(0, -1) : state.pendingControls;
    } else if (tok === "return" || tok === "throw" || tok === "break" || tok === "continue") {
      state.pendingControls = isTopDepth ? state.pendingControls.slice(0, -1) : state.pendingControls;
    } else if (tok === "}" && state.scopeStack.length > 0 && state.scopeStack[state.scopeStack.length - 1].isCondBrace) {
      state.scopeStack.pop();
      state.braceDepth = Math.max(0, state.braceDepth - 1);
    } else if (tok === "}" && state.scopeStack.length > 0) {
      const popped = state.scopeStack.pop();
      popped.flags.push(...(state.parenDepth === 0 ? state.pendingFlags : []));
      const parentScope = state.scopeStack.length > 0 ? state.scopeStack[state.scopeStack.length - 1] : null;
      const mergeUp = popped.isControl && parentScope !== null;
      const closedEntry = { startLine: popped.startLine, openLine: popped.openLine, prevEndLine: popped.prevEndLine, flags: popped.flags };
      (mergeUp ? parentScope.flags : state.closedScopes).push(...(mergeUp ? popped.flags : [closedEntry]));
      state.braceDepth = Math.max(0, state.braceDepth - 1);
      state.lastClosedEnd = lineNo;
      state.pendingFlags = state.parenDepth === 0 ? [] : state.pendingFlags;
      state.pendingStart = -1;
      state.declKind = "";
      state.seenArrow = false;
    } else if (tok === "}") {
      state.braceDepth = Math.max(0, state.braceDepth - 1);
      state.pendingStart = -1;
      state.declKind = "";
      state.seenArrow = false;
    } else if (tok === "{" && top !== null && !isTopDepth) {
      state.scopeStack.push({ startLine: lineNo, openLine: lineNo, prevEndLine: state.lastClosedEnd, flags: [], isControl: false, isCondBrace: true });
      state.braceDepth += 1;
    } else if (tok === "{") {
      const scopeStart = state.pendingStart !== -1 ? state.pendingStart : lineNo;
      state.scopeStack.push({ startLine: scopeStart, openLine: lineNo, prevEndLine: state.lastClosedEnd, flags: [...state.pendingFlags], isControl: isTopDepth, isCondBrace: false });
      state.braceDepth += 1;
      state.pendingStart = -1;
      state.pendingFlags = [];
      state.declKind = "";
      state.seenArrow = false;
      state.pendingControls = isTopDepth ? state.pendingControls.slice(0, -1) : state.pendingControls;
    } else if (TS_CONTROL_WORDS.has(tok) && !isTopDepth) {
      state.pendingControls.push([state.parenDepth, state.bracketDepth, state.braceDepth]);
    } else if (boolName && state.scopeStack.length > 0 && state.parenDepth === 0) {
      state.scopeStack[state.scopeStack.length - 1].flags.push(boolName);
    } else if (boolName) {
      state.pendingFlags.push(boolName);
    }
  }
  if (state.pendingStart === -1 && lineText.slice(cursor).trim().length > 0) { state.pendingStart = lineNo; }
  const isValueDone = state.declKind === "value" && state.parenDepth === 0 && state.bracketDepth === 0 && !/(?:=>|[=(\[,?:+\-*/.]|&&|\|\|)$/.test(lineText.trimEnd());
  if (isValueDone && state.scopeStack.length > 0) { state.scopeStack[state.scopeStack.length - 1].flags.push(...state.pendingFlags); }
  if (isValueDone) { state.pendingFlags = []; state.pendingStart = -1; state.declKind = ""; state.seenArrow = false; }
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
  let outline = false;
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
    if (token === "--outline") {
      outline = true;
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
  if (paths.length === 0 || (outline && (applyFix || dryRun))) {
    return null;
  }
  return { paths, asJson, applyFix, dryRun, outline, showBackend, backend };
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
  const state = {
    out: "",
    comments: [],
    rawLineComments: new Set(),
    rawAllComments: new Set(),
    line: 1,
    idx: 0,
    lastToken: "",
    exprBraceStack: [],
  };
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
    if (ch === "}" && state.exprBraceStack.length > 0 && state.exprBraceStack[state.exprBraceStack.length - 1] === 0) {
      state.exprBraceStack.pop();
      state.out += ",";
      consumeTemplateLiteral(source, state);
      continue;
    }
    const canRegex = !(isJsx && state.lastToken === ">") && (REGEX_PRECEDING_CHARS.has(state.lastToken) || REGEX_PRECEDING_KEYWORDS.has(state.lastToken));
    if (ch === "/" && canRegex) {
      consumeRegexLiteral(source, state);
      continue;
    }
    advanceCodeChar(source, state, ch);
  }
  const cleanedLines = state.out.split("\n");
  const commentLines = new Set(
    [...state.rawAllComments].filter((ln) => ln >= 1 && ln <= cleanedLines.length && cleanedLines[ln - 1].trim() === ""),
  );
  const lineCommentLines = new Set([...state.rawLineComments].filter((ln) => commentLines.has(ln)));
  return { cleaned: state.out, comments: state.comments, commentLines, lineCommentLines };
}

function consumeLineComment(source, state) {
  const start = state.idx;
  const startLine = state.line;
  while (state.idx < source.length && source[state.idx] !== "\n") {
    state.idx += 1;
  }
  state.comments.push({ line: startLine, text: source.slice(start, state.idx) });
  state.rawLineComments.add(startLine);
  state.rawAllComments.add(startLine);
}

function consumeBlockComment(source, state) {
  state.idx += 2;
  state.rawAllComments.add(state.line);
  while (state.idx < source.length) {
    const ch = source[state.idx];
    if (ch === "*" && source[state.idx + 1] === "/") {
      state.rawAllComments.add(state.line);
      state.idx += 2;
      return;
    }
    if (ch === "\n") {
      state.out += "\n";
      state.line += 1;
      state.rawAllComments.add(state.line);
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
  } else if (ch === "}" && state.exprBraceStack.length > 0) {
    state.exprBraceStack[state.exprBraceStack.length - 1] -= 1;
  }
  if (ch === "\n") {
    state.line += 1;
  }
  if (!/\s/.test(ch)) {
    let start = state.idx;
    while (/[A-Za-z0-9_$]/.test(ch) && start > 0 && /[A-Za-z0-9_$]/.test(source[start - 1])) {
      start -= 1;
    }
    state.lastToken = /[A-Za-z0-9_$]/.test(ch) ? source.slice(start, state.idx + 1) : ch;
  }
  state.out += ch;
  state.idx += 1;
}

function extractFunctions(lines) {
  const state = {
    spans: [],
    braceDepth: 0,
    parenDepth: 0,
    bracketDepth: 0,
    pendingStart: -1,
    pendingKind: "",
    seenArrow: false,
    typeBraceDepth: 0,
    activeStart: -1,
    activeBodyStart: -1,
    activeBodyCol: 0,
    activeBase: 0,
  };
  for (let idx = 0; idx < lines.length; idx += 1) {
    advanceFnSpanLine(lines[idx], idx + 1, state);
  }
  return state.spans;
}

function advanceFnSpanLine(lineText, lineNo, state) {
  let segStart = 0;
  for (const match of lineText.matchAll(FN_EVENT_PATTERN)) {
    const pos = match.index;
    const tok = match[0];
    const declSlice = lineText.slice(segStart, pos + (tok === "(" ? 1 : 0)).replace(/^[};\s]+/, "");
    const canStartDecl = state.activeStart === -1 && state.braceDepth === 0 && state.parenDepth === 0 && state.bracketDepth === 0;
    if (canStartDecl && FUNC_DECL_PATTERN.test(declSlice)) {
      state.pendingStart = lineNo;
      state.pendingKind = "fn";
      state.seenArrow = false;
      state.typeBraceDepth = 0;
    } else if (canStartDecl && tok === "(" && ARROW_DECL_PATTERN.test(declSlice)) {
      state.pendingStart = lineNo;
      state.pendingKind = "arrow";
      state.seenArrow = false;
      state.typeBraceDepth = 0;
    }
    if (tok === "=>") {
      state.seenArrow = state.parenDepth === 0 && state.typeBraceDepth === 0 ? true : state.seenArrow;
    } else if (tok === "(" || tok === ")") {
      state.parenDepth = Math.max(0, state.parenDepth + (PAREN_DELTAS[tok] ?? 0));
    } else if (tok === "[" || tok === "]") {
      state.bracketDepth = Math.max(0, state.bracketDepth + (BRACKET_DELTAS[tok] ?? 0));
      segStart = tok === "]" && state.bracketDepth === 0 && state.activeStart === -1 && state.pendingStart === -1 ? pos + 1 : segStart;
    } else if (tok === ";" && state.parenDepth === 0 && state.bracketDepth === 0 && state.typeBraceDepth === 0) {
      state.pendingStart = -1;
      state.pendingKind = "";
      segStart = state.activeStart === -1 ? pos + 1 : segStart;
    } else if (tok === "{" && state.pendingStart !== -1 && state.parenDepth === 0 && state.typeBraceDepth === 0) {
      const preText = lineText.slice(segStart, pos).trimEnd();
      const isTypeObj = (state.pendingKind === "fn" || !state.seenArrow) && /[:|&]$/.test(preText);
      const invalidArrow = state.pendingKind === "arrow" && !state.seenArrow && !isTypeObj;
      state.typeBraceDepth += isTypeObj ? 1 : 0;
      const opensBody = !isTypeObj && !invalidArrow;
      state.activeStart = opensBody ? state.pendingStart : state.activeStart;
      state.activeBodyStart = opensBody ? lineNo : state.activeBodyStart;
      state.activeBodyCol = opensBody ? pos : state.activeBodyCol;
      state.activeBase = opensBody ? state.braceDepth : state.activeBase;
      state.pendingStart = isTypeObj ? state.pendingStart : -1;
      state.pendingKind = isTypeObj ? state.pendingKind : "";
    } else if (tok === "}" && state.typeBraceDepth > 0) {
      state.typeBraceDepth -= 1;
    }
    if (tok === "{") {
      state.braceDepth += 1;
      segStart = pos + 1;
    } else if (tok === "}") {
      state.braceDepth = Math.max(0, state.braceDepth - 1);
      segStart = pos + 1;
    }
    if (tok === "}" && state.activeStart !== -1 && state.braceDepth <= state.activeBase) {
      state.spans.push({ startLine: state.activeStart, bodyStartLine: state.activeBodyStart, endLine: lineNo, bodyStartCol: state.activeBodyCol, endCol: pos + 1 });
      state.activeStart = -1;
    }
  }
  const tail = lineText.slice(segStart).replace(/^[};\s]+/, "");
  if (state.activeStart === -1 && state.pendingStart === -1 && state.braceDepth === 0 && FUNC_DECL_PATTERN.test(tail)) {
    state.pendingStart = lineNo;
    state.pendingKind = "fn";
    state.seenArrow = false;
    state.typeBraceDepth = 0;
  } else if (state.pendingStart !== -1 && state.pendingKind === "arrow" && state.parenDepth === 0 && state.typeBraceDepth === 0 && state.seenArrow && /=>\s*[^({\s]/.test(lineText.slice(segStart))) {
    state.pendingStart = -1;
    state.pendingKind = "";
  }
}

function measureControlNesting(lines, fnSpan) {
  const bodyStartCol = fnSpan.bodyStartCol ?? 0;
  const endCol = fnSpan.endCol ?? null;
  const chunks = [];
  for (let ln = fnSpan.bodyStartLine; ln <= fnSpan.endLine; ln += 1) {
    const raw = lines[ln - 1] ?? "";
    const start = ln === fnSpan.bodyStartLine ? bodyStartCol : 0;
    const end = ln === fnSpan.endLine && endCol !== null ? endCol : raw.length;
    chunks.push(raw.slice(start, end));
  }
  const bodyText = chunks.join("\n");
  const controlStack = [];
  let pendingControls = [];
  let braceDepth = 0;
  let parenDepth = 0;
  let bracketDepth = 0;
  let maxNesting = 0;
  for (const match of bodyText.matchAll(TS_NESTING_TOKEN_PATTERN)) {
    const tok = match[0];
    if (tok.startsWith("//") || (braceDepth === 0 && tok !== "{")) {
      continue;
    }
    const top = pendingControls.length > 0 ? pendingControls[pendingControls.length - 1] : null;
    const isTopDepth = top !== null && top[0] === parenDepth && top[1] === bracketDepth && top[2] === braceDepth;
    if (tok === "(" || tok === ")" || tok === "[" || tok === "]") {
      parenDepth = Math.max(0, parenDepth + (PAREN_DELTAS[tok] ?? 0));
      bracketDepth = Math.max(0, bracketDepth + (BRACKET_DELTAS[tok] ?? 0));
    } else if (tok === ";" || tok === "return" || tok === "throw" || tok === "break" || tok === "continue") {
      pendingControls = isTopDepth ? pendingControls.slice(0, -1) : pendingControls;
    } else if (tok === "{" && isTopDepth) {
      controlStack.push(braceDepth);
      maxNesting = Math.max(maxNesting, controlStack.length);
      pendingControls = pendingControls.slice(0, -1);
      braceDepth += 1;
    } else if (tok === "{") {
      braceDepth += 1;
    } else if (tok === "}") {
      braceDepth = Math.max(0, braceDepth - 1);
    } else if (TS_CONTROL_WORDS.has(tok) && !isTopDepth) {
      pendingControls.push([parenDepth, bracketDepth, braceDepth]);
    }
    while (tok === "}" && controlStack.length > 0 && braceDepth <= controlStack[controlStack.length - 1]) {
      controlStack.pop();
    }
  }
  return maxNesting;
}

function findNestedFunctions(lines, fnSpan, filePath) {
  const violations = [];
  for (let lineNo = fnSpan.bodyStartLine; lineNo <= fnSpan.endLine; lineNo += 1) {
    const raw = lines[lineNo - 1] ?? "";
    const start = lineNo === fnSpan.bodyStartLine ? (fnSpan.bodyStartCol ?? 0) + 1 : 0;
    const end = lineNo === fnSpan.endLine && fnSpan.endCol != null ? fnSpan.endCol : raw.length;
    if (/\bfunction\s+[A-Za-z_$]/.test(raw.slice(start, end))) {
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

function filterSuppressed(violations, functions, { directives, rawLines, commentLines }) {
  const spanHeaders = functions.map((fnSpan) => {
    const outer = fnSpan.outerStartLine ?? fnSpan.startLine;
    const prevEnd = Math.max(
      0,
      ...functions
        .filter((other) => other !== fnSpan && other.endLine <= outer && other.startLine <= outer)
        .map((other) => other.endLine),
    );
    const leading = collectContiguousHeaderLines(rawLines, outer, {
      prevEndLine: prevEnd,
      commentLines,
    });
    for (let ln = outer; ln <= fnSpan.startLine; ln += 1) {
      leading.add(ln);
    }
    return { outerStartLine: outer, startLine: fnSpan.startLine, endLine: fnSpan.endLine, headerLines: leading };
  });
  const declStartLines = new Set(spanHeaders.flatMap((span) => [span.outerStartLine, span.startLine]));
  return violations.filter((entry) => {
    const matchedLines = spanHeaders
      .filter(
        (span) =>
          span.outerStartLine <= entry.line &&
          entry.line <= span.endLine &&
          !(span.outerStartLine < span.endLine && entry.line === span.endLine && declStartLines.has(entry.line)),
      )
      .flatMap((span) => [...span.headerLines]);
    const candidateLines = new Set([entry.line, ...matchedLines]);
    if (commentLines.has(entry.line - 1) && !declStartLines.has(entry.line)) {
      candidateLines.add(entry.line - 1);
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
    if (tsMod.isFunctionDeclaration(stmt) && stmt.name && stmt.body) {
      const startLine = sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1;
      functions.push({
        name: stmt.name.text,
        node: stmt,
        startLine,
        outerStartLine: startLine,
        endLine: sf.getLineAndCharacterOfPosition(stmt.getEnd()).line + 1,
      });
    } else if (tsMod.isVariableStatement(stmt) && (stmt.declarationList.flags & tsMod.NodeFlags.Const) !== 0) {
      const outerStartLine = sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1;
      const decls = stmt.declarationList.declarations.filter(
        (decl) =>
          tsMod.isIdentifier(decl.name) &&
          decl.initializer &&
          tsMod.isArrowFunction(decl.initializer) &&
          tsMod.isBlock(decl.initializer.body),
      );
      functions.push(
        ...decls.map((decl) => ({
          name: decl.name.text,
          node: decl.initializer,
          startLine: sf.getLineAndCharacterOfPosition(decl.getStart(sf)).line + 1,
          outerStartLine,
          endLine: sf.getLineAndCharacterOfPosition(decl.initializer.getEnd()).line + 1,
        })),
      );
    }
  }
  return functions;
}

function collectAstMemberFunctions(sf, tsMod) {
  const members = [];
  for (const stmt of sf.statements) {
    const isNonConstVar = tsMod.isVariableStatement(stmt) && (stmt.declarationList.flags & tsMod.NodeFlags.Const) === 0;
    const decls = isNonConstVar
      ? stmt.declarationList.declarations.filter(
          (decl) => tsMod.isIdentifier(decl.name) && decl.initializer && tsMod.isArrowFunction(decl.initializer) && tsMod.isBlock(decl.initializer.body),
        )
      : [];
    const outerVarStart = sf.getLineAndCharacterOfPosition(stmt.getStart(sf)).line + 1;
    members.push(
      ...decls.map((decl) => ({
        name: decl.name.text,
        node: decl.initializer,
        startLine: sf.getLineAndCharacterOfPosition(decl.getStart(sf)).line + 1,
        outerStartLine: outerVarStart,
        endLine: sf.getLineAndCharacterOfPosition(decl.initializer.getEnd()).line + 1,
      })),
    );
    const methods = tsMod.isClassDeclaration(stmt)
      ? stmt.members.filter((member) => tsMod.isMethodDeclaration(member) && member.body && member.name)
      : [];
    members.push(
      ...methods.map((member) => {
        const firstNonDec = (member.modifiers ?? []).find((mod) => !tsMod.isDecorator(mod));
        const sigPos = firstNonDec ? firstNonDec.getStart(sf) : member.name.getStart(sf);
        return {
          name: member.name.getText(sf),
          node: member,
          startLine: sf.getLineAndCharacterOfPosition(sigPos).line + 1,
          outerStartLine: sf.getLineAndCharacterOfPosition(member.getStart(sf)).line + 1,
          endLine: sf.getLineAndCharacterOfPosition(member.getEnd()).line + 1,
        };
      }),
    );
  }
  return members;
}

function collectAstModuleTierContext(topFunctions, filePath, { rawLines, lineCommentLines, commentLines, tsMod }) {
  const fnKeys = topFunctions.map((entry) => `${entry.name}@${entry.startLine}`);
  const keyToName = new Map(topFunctions.map((entry, idx) => [fnKeys[idx], entry.name]));
  const keysByName = new Map();
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const bucket = keysByName.get(topFunctions[idx].name) ?? [];
    bucket.push(fnKeys[idx]);
    keysByName.set(topFunctions[idx].name, bucket);
  }
  const callGraph = new Map();
  const declaredTags = new Map();
  const multiTagViolations = [];
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const entry = topFunctions[idx];
    const entryKey = fnKeys[idx];
    const callees = collectAstCalls(entry.node, keysByName, tsMod);
    callees.delete(entryKey);
    callGraph.set(entryKey, callees);
    const outer = entry.outerStartLine ?? entry.startLine;
    const prevEnd = Math.max(
      0,
      ...topFunctions.filter((other) => other !== entry && other.endLine <= outer && other.startLine <= outer).map((other) => other.endLine),
    );
    const leading = collectContiguousHeaderLines(rawLines, outer, { prevEndLine: prevEnd, commentLines });
    for (let ln = outer; ln < entry.startLine; ln += 1) {
      leading.add(ln);
    }
    const { tag, hasMultiple } = extractAstTierTags(rawLines, leading, lineCommentLines);
    declaredTags.set(entryKey, tag);
    if (hasMultiple) {
      multiTagViolations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' has multiple tier tags -> keep a single [L0]..[L3] tag.`,
      });
    }
  }
  return { callGraph, declaredTags, fnKeys, keyToName, multiTagViolations };
}

function buildAstOutlineEntries(topFunctions, { callGraph, declaredTags, fnKeys, keyToName }) {
  const distMap = computeAllPairsLongestPaths(fnKeys, callGraph);
  const boundsMap = computeTierBounds(fnKeys, distMap, declaredTags);
  return topFunctions.map((entry, idx) => {
    const entryKey = fnKeys[idx];
    const tagVal = declaredTags.get(entryKey);
    const callers = [...new Set(fnKeys.filter((callerKey) => (callGraph.get(callerKey) ?? new Set()).has(entryKey)).map((key) => keyToName.get(key)))].sort();
    const callees = [...new Set([...(callGraph.get(entryKey) ?? new Set())].map((key) => keyToName.get(key)))].sort();
    return {
      name: entry.name,
      start_line: entry.startLine,
      end_line: entry.endLine,
      line_count: entry.endLine - entry.startLine + 1,
      declared_tier: tagVal !== null ? `L${tagVal}` : null,
      valid_tiers: boundsMap.get(entryKey).validTiers,
      callers,
      callees,
    };
  });
}

function checkAstOrderAndDepth(topFunctions, filePath, tsMod) {
  const violations = [];
  const fnKeys = topFunctions.map((entry) => `${entry.name}@${entry.startLine}`);
  const keyToName = new Map(topFunctions.map((entry, idx) => [fnKeys[idx], entry.name]));
  const keyToLine = new Map(topFunctions.map((entry, idx) => [fnKeys[idx], entry.startLine]));
  const keysByName = new Map();
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const bucket = keysByName.get(topFunctions[idx].name) ?? [];
    bucket.push(fnKeys[idx]);
    keysByName.set(topFunctions[idx].name, bucket);
  }
  const callGraph = new Map();
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const entry = topFunctions[idx];
    const entryKey = fnKeys[idx];
    const callees = collectAstCalls(entry.node, keysByName, tsMod);
    callGraph.set(entryKey, callees);
    const outOfOrder = [...callees]
      .filter((calleeKey) => calleeKey !== entryKey && keyToLine.get(calleeKey) < entry.startLine)
      .sort((left, right) => keyToName.get(left).localeCompare(keyToName.get(right)) || keyToLine.get(left) - keyToLine.get(right));
    for (const calleeKey of outOfOrder) {
      const calleeName = keyToName.get(calleeKey);
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' calls '${calleeName}' defined above it -> move '${calleeName}' below '${entry.name}' for top-down order.`,
      });
    }
  }
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const entry = topFunctions[idx];
    const { depth, leafCallsLocal } = computeAstChainDepth(fnKeys[idx], callGraph);
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

function checkAstTierTags(topFunctions, filePath, { callGraph, declaredTags, fnKeys, keyToName }) {
  const violations = [];
  const distMap = computeAllPairsLongestPaths(fnKeys, callGraph);
  const boundsMap = computeTierBounds(fnKeys, distMap, declaredTags);
  for (let idx = 0; idx < topFunctions.length; idx += 1) {
    const entry = topFunctions[idx];
    const entryKey = fnKeys[idx];
    const tagVal = declaredTags.get(entryKey);
    if (tagVal === null || tagVal === undefined) {
      continue;
    }
    const { dMax, height, lb0, ub0, isAdapter } = boundsMap.get(entryKey);
    const valid0 = [0, 1, 2, 3].filter((tier) => lb0 <= tier && tier <= ub0).map((tier) => `[L${tier}]`);
    const badAncKey = fnKeys.find((ancKey) => {
      const ancTag = declaredTags.get(ancKey);
      const dist = distMap.get(`${ancKey}->${entryKey}`);
      return (
        ancKey !== entryKey &&
        ancTag !== null &&
        ancTag !== undefined &&
        dist !== undefined &&
        !(dist === 1 && ancTag === 0 && tagVal === 0 && isAdapter) &&
        tagVal - ancTag < dist
      );
    });
    if (tagVal === 3 && height > 0) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "CANON-01",
        message: `'${entry.name}' is tagged [L3] (leaf) but calls local functions -> update tag or move local calls to L1/L2.`,
      });
    } else if (tagVal < lb0 || tagVal > ub0) {
      const message =
        valid0.length > 0
          ? `'${entry.name}' is tagged [L${tagVal}] outside valid tier range (${valid0.join(", ")}) for depth ${dMax} and height ${height} -> update tier tag.`
          : `'${entry.name}' is tagged [L${tagVal}] in a call chain exceeding L0->L3 ceiling (depth ${dMax}, height ${height}) -> flatten call depth.`;
      violations.push({ path: filePath, line: entry.startLine, rule: "CANON-01", message });
    } else if (badAncKey) {
      const ancName = keyToName.get(badAncKey);
      const ancTag = declaredTags.get(badAncKey);
      const ancDist = distMap.get(`${badAncKey}->${entryKey}`);
      const requiredTier = ancTag + ancDist;
      const message =
        requiredTier <= 3
          ? `'${entry.name}' is tagged [L${tagVal}] at call distance ${ancDist} below '${ancName}' ([L${ancTag}]) -> callee tier must be at least [L${requiredTier}].`
          : `'${entry.name}' is tagged [L${tagVal}] at call distance ${ancDist} below '${ancName}' ([L${ancTag}]), which exceeds L0->L3 depth -> update '${ancName}' tier tag or flatten call depth.`;
      violations.push({ path: filePath, line: entry.startLine, rule: "CANON-01", message });
    }
  }
  return violations;
}

export function checkAstDuplicateLeaves(topFunctions, ctx) {
  const { filePath, rawLines, commentLines, callGraph, declaredTags, tsMod } = ctx;
  const sf = ctx.sf ?? tsMod.createSourceFile(filePath, rawLines.join("\n"), tsMod.ScriptTarget.Latest, true);
  const violations = [];
  const seenSkeletons = new Map();
  for (const entry of topFunctions) {
    const fnNode = entry.node;
    if (!fnNode.body || !tsMod.isBlock(fnNode.body)) {
      continue;
    }
    const entryKey = `${entry.name}@${entry.startLine}`;
    const rawCallees = callGraph.get(entryKey) ?? callGraph.get(entry.name) ?? new Set();
    const callees = new Set(rawCallees);
    callees.delete(entryKey);
    callees.delete(entry.name);
    const tagVal = declaredTags.has(entryKey) ? declaredTags.get(entryKey) : declaredTags.get(entry.name);
    if (callees.size > 0 || tagVal === 0) {
      continue;
    }
    if (countTsLeafBodyLines(fnNode, rawLines, { commentLines, sf }) < 6) {
      continue;
    }
    const skeleton = normalizeTsLeafSkeleton(fnNode, tsMod);
    const firstSeen = seenSkeletons.get(skeleton);
    if (firstSeen) {
      violations.push({
        path: filePath,
        line: entry.startLine,
        rule: "LLM-08",
        message: `Function '${entry.name}' duplicates leaf helper '${firstSeen.name}' (line ${firstSeen.startLine}); reuse the existing helper instead of adding a duplicate.`,
      });
      continue;
    }
    seenSkeletons.set(skeleton, { name: entry.name, startLine: entry.startLine });
  }
  return violations;
}

export function countTsLeafBodyLines(fnNode, rawLines, { commentLines, sf }) {
  const stmts = fnNode.body?.statements;
  if (!stmts || stmts.length === 0) {
    return 0;
  }
  const startLine = sf.getLineAndCharacterOfPosition(stmts[0].getStart(sf)).line + 1;
  const endLine = sf.getLineAndCharacterOfPosition(stmts[stmts.length - 1].getEnd()).line + 1;
  let count = 0;
  for (let lineNo = startLine; lineNo <= endLine; lineNo += 1) {
    const trimmed = (rawLines[lineNo - 1] ?? "").trim();
    if (trimmed && !commentLines.has(lineNo) && trimmed !== "{" && trimmed !== "}" && trimmed !== "};") {
      count += 1;
    }
  }
  return count;
}

export function normalizeTsLeafSkeleton(fnNode, tsMod) {
  const env = new Map();
  const scopeStack = [];
  const tokens = [];
  let nextVar = 0;
  const stack = [["FN", fnNode]];
  while (stack.length > 0) {
    const [tag, node] = stack.pop();
    if (tag === "EMIT") { tokens.push(node); }
    else if (tag === "POP_SCOPE") { scopeStack.pop().forEach((prev, varName) => (prev === null ? env.delete(varName) : env.set(varName, prev))); }
    else if (tag === "BIND" && (node.alias || tsMod.isIdentifier(node))) {
      const saved = scopeStack[scopeStack.length - 1];
      const need = !saved.has(node.text);
      const assigned = need ? (node.alias || `$v${nextVar}`) : env.get(node.text);
      nextVar += need && !node.alias ? 1 : 0;
      saved.set(node.text, need ? (env.get(node.text) ?? null) : saved.get(node.text));
      env.set(node.text, assigned);
      tokens.push(...(node.alias ? [] : [["ID", assigned]]));
    } else if (tag === "BIND" && tsMod.isBindingElement(node)) {
      const isObjId = tsMod.isObjectBindingPattern(node.parent) && tsMod.isIdentifier(node.name);
      const propNode = node.propertyName;
      const staticProp = propNode && (tsMod.isIdentifier(propNode) || tsMod.isStringLiteral(propNode) || tsMod.isNumericLiteral(propNode));
      const fallbackName = isObjId ? node.name.text : null;
      tokens.push(["BIND_EL", isObjId ? "OBJ" : "PAT", staticProp ? propNode.text : fallbackName, Boolean(node.dotDotDotToken)]);
      stack.push(...(node.initializer ? [["AST", node.initializer]] : []), ["BIND", node.name], ...(propNode && !staticProp ? [["AST", propNode]] : []));
    } else if (tag === "BIND") {
      const children = [];
      node.forEachChild((child) => { children.push(["BIND", child]); });
      tokens.push(["OPEN", node.kind]);
      stack.push(["EMIT", ["CLOSE", node.kind]], ...children.reverse());
    } else if (tag === "FN" || tsMod.isArrowFunction(node) || tsMod.isFunctionExpression(node)) {
      const isAsync = (node.modifiers ?? []).some((mod) => mod.kind === tsMod.SyntaxKind.AsyncKeyword);
      const params = node.parameters ?? [];
      const shape = params.map((param) => `${param.dotDotDotToken ? "..." : ""}${param.questionToken ? "?" : ""}`);
      tokens.push(["SIG", node === fnNode, isAsync, params.length, shape]);
      scopeStack.push(new Map());
      const bodyNodes = tsMod.isBlock(node.body) ? [...node.body.statements] : [node.body];
      const paramOps = params.map((param, idx) => {
        const bindOp = node === fnNode && tsMod.isIdentifier(param.name) ? ["BIND", { text: param.name.text, alias: `$p${idx}` }] : ["BIND", param.name];
        return [bindOp, param.initializer ? ["AST", param.initializer] : ["EMIT", ["NO_DEFAULT"]]];
      }).reverse().flat();
      stack.push(["EMIT", ["END_FN"]], ["POP_SCOPE", null], ...bodyNodes.reverse().map((stmt) => ["AST", stmt]), ["EMIT", ["END_PARAMS"]], ...paramOps);
    } else if (tsMod.isPrefixUnaryExpression(node) || tsMod.isPostfixUnaryExpression(node)) {
      tokens.push(["UNARY", node.kind, node.operator]);
      stack.push(["EMIT", ["END_UNARY"]], ["AST", node.operand]);
    } else if (tsMod.isBinaryExpression(node)) {
      tokens.push(["BIN", node.operatorToken.kind]);
      stack.push(["EMIT", ["END_BIN"]], ["AST", node.right], ["AST", node.left]);
    } else if (tsMod.isVariableDeclarationList(node)) {
      tokens.push(["VAR_LIST", node.flags & (tsMod.NodeFlags.Const | tsMod.NodeFlags.Let)]);
      stack.push(["EMIT", ["END_VAR_LIST"]], ...[...node.declarations].reverse().map((decl) => ["AST", decl]));
    } else if (tsMod.isVariableDeclaration(node)) {
      tokens.push(["VAR_DECL"]);
      stack.push(["EMIT", ["END_VAR_DECL"]], ...(node.initializer ? [["AST", node.initializer]] : []), ["BIND", node.name]);
    } else if (tsMod.isShorthandPropertyAssignment(node)) {
      tokens.push(["SHORTHAND", node.name.text, env.get(node.name.text) ?? node.name.text]);
      stack.push(...(node.objectAssignmentInitializer ? [["AST", node.objectAssignmentInitializer]] : []));
    } else if (tsMod.isPropertyAccessExpression(node)) {
      tokens.push(["PROP_ACCESS", Boolean(node.questionDotToken), node.name.text]);
      stack.push(["EMIT", ["END_PROP"]], ["AST", node.expression]);
    } else if (tsMod.isPropertyAssignment(node)) {
      const staticProp = tsMod.isIdentifier(node.name) || tsMod.isStringLiteral(node.name) || tsMod.isNumericLiteral(node.name);
      tokens.push(["PROP_ASSIGN", staticProp ? node.name.text : null]);
      stack.push(["EMIT", ["END_PROP_ASSIGN"]], ["AST", node.initializer], ...(staticProp ? [] : [["AST", node.name]]));
    } else if (tsMod.isIdentifier(node)) { tokens.push(["ID", env.get(node.text) ?? node.text]); }
    else if (tsMod.isLiteralExpression(node) || tsMod.isTemplateLiteralToken(node)) { tokens.push(["LIT", node.kind, node.text]); }
    else if (!tsMod.isTypeNode(node)) {
      const isScoped = tsMod.isBlock(node) || tsMod.isCatchClause(node) || tsMod.isForOfStatement(node) || tsMod.isForInStatement(node) || tsMod.isForStatement(node);
      const children = [];
      node.forEachChild((child) => { children.push(["AST", child]); });
      scopeStack.push(...(isScoped ? [new Map()] : []));
      tokens.push(["OPEN", node.kind]);
      stack.push(["EMIT", ["CLOSE", node.kind]], ...(isScoped ? [["POP_SCOPE", null]] : []), ...children.reverse());
    }
  }
  return JSON.stringify(tokens);
}

function checkAstFunctionRules(topFunctions, { sf, tsMod, filePath }) {
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
    violations.push(...scanAstFunctionBody(entry, { sf, tsMod, filePath }));
  }
  return violations;
}

function scanAstFunctionBody(entry, { sf, tsMod, filePath }) {
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
    const unwrapParen = (expr) => (expr && tsMod.isParenthesizedExpression(expr) ? expr.expression : expr);
    if (tsMod.isFunctionDeclaration(node) && node !== entry.node) {
      violations.push({ path: filePath, line, rule: "CANON-05", message: "Nested function declaration is banned -> lift to module top level." });
    } else if (
      tsMod.isConditionalExpression(node) &&
      (tsMod.isConditionalExpression(unwrapParen(node.whenTrue)) || tsMod.isConditionalExpression(unwrapParen(node.whenFalse)))
    ) {
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

function collectAstCalls(fnNode, keysByName, tsMod) {
  const callees = new Set();
  const stack = [
    fnNode.body,
    ...(fnNode.parameters ?? []).map((param) => param.initializer).filter(Boolean),
  ];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current) {
      continue;
    }
    const matched =
      tsMod.isCallExpression(current) && tsMod.isIdentifier(current.expression)
        ? keysByName.get(current.expression.text) ?? []
        : [];
    matched.forEach((matchedKey) => callees.add(matchedKey));
    current.forEachChild((child) => {
      stack.push(child);
    });
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

function readFileText(filePath) {
  try {
    return { source: fs.readFileSync(filePath, "utf8"), error: null };
  } catch (err) {
    return { source: "", error: `Error reading ${filePath}: ${err.message}` };
  }
}

function formatOutlineText(payload) {
  const blocks = [];
  for (const fileEntry of payload) {
    const lines = [fileEntry.path];
    if (!Array.isArray(fileEntry.functions) || fileEntry.functions.length === 0) {
      lines.push("  (no top-level functions)");
      blocks.push(lines.join("\n"));
      continue;
    }
    for (const fnInfo of fileEntry.functions) {
      const valid = fnInfo.valid_tiers;
      const rangeBadge = valid.length > 1 ? `[${valid[0]}..${valid[valid.length - 1]}]` : "[invalid]";
      const inferredBadge = valid.length === 1 ? `[${valid[0]}]` : rangeBadge;
      const badge = fnInfo.declared_tier ? `[${fnInfo.declared_tier}]` : inferredBadge;
      const callersPart = fnInfo.callers.length > 0 ? ` <- ${fnInfo.callers.join(", ")}` : "";
      const calleesPart = fnInfo.callees.length > 0 ? ` -> ${fnInfo.callees.join(", ")}` : "";
      const span = `(L${fnInfo.start_line}-L${fnInfo.end_line}, ${fnInfo.line_count}L)`;
      lines.push(`  ${badge} ${fnInfo.name} ${span}${callersPart}${calleesPart}`);
    }
    blocks.push(lines.join("\n"));
  }
  return blocks.join("\n");
}

function collectContiguousHeaderLines(rawLines, outerStart, { prevEndLine, commentLines }) {
  const collected = new Set();
  const attrStack = [];
  const attrLines = [];
  const pendingDelims = [];
  let curCode = "";
  let lineNo = outerStart - 1;
  while ((lineNo > prevEndLine && lineNo >= 1) || pendingDelims.length > 0 || (attrLines.length > 0 && attrStack.length === 0)) {
    if (pendingDelims.length > 0 && ")]}".includes(pendingDelims[pendingDelims.length - 1])) {
      attrStack.push(pendingDelims.pop());
      continue;
    }
    if (pendingDelims.length > 0 && (attrStack.length === 0 || TS_HEADER_PAIR_MAP[attrStack[attrStack.length - 1]] !== pendingDelims[pendingDelims.length - 1])) {
      return collected;
    }
    if (pendingDelims.length > 0) {
      pendingDelims.pop();
      attrStack.pop();
      continue;
    }
    if (attrLines.length > 0 && attrStack.length === 0 && curCode.startsWith("@")) {
      attrLines.forEach((ln) => collected.add(ln));
      attrLines.length = 0;
      continue;
    }
    if (attrLines.length > 0 && attrStack.length === 0) {
      break;
    }
    if (lineNo <= prevEndLine || lineNo < 1) {
      break;
    }
    const rawLine = rawLines[lineNo - 1] ?? "";
    const trimmed = rawLine.trim();
    if (attrStack.length === 0 && lineNo === 1 && TS_HEADER_COMMENT_PATTERN.test(trimmed)) {
      break;
    }
    if (attrStack.length === 0 && commentLines.has(lineNo)) {
      collected.add(lineNo);
      lineNo -= 1;
      continue;
    }
    if (attrStack.length > 0 && commentLines.has(lineNo)) {
      attrLines.push(lineNo);
      lineNo -= 1;
      continue;
    }
    curCode = rawLine.replace(TS_HEADER_NOISE_PATTERN, " ").trim();
    if (attrStack.length === 0 && curCode.startsWith("@") && !/[)\]]$/.test(curCode)) {
      collected.add(lineNo);
      lineNo -= 1;
      continue;
    }
    if (attrStack.length === 0 && !curCode.endsWith(")") && !curCode.endsWith("]")) {
      break;
    }
    attrLines.push(lineNo);
    pendingDelims.push(...[...curCode].filter((ch) => "[](){}".includes(ch)));
    lineNo -= 1;
  }
  return collected;
}

function extractAstTierTags(rawLines, headerLines, lineCommentLines) {
  const matched = [...headerLines]
    .filter((lineNo) => lineCommentLines.has(lineNo))
    .sort((left, right) => left - right)
    .map((lineNo) => TIER_TAG_PATTERN.exec(rawLines[lineNo - 1] ?? ""))
    .filter(Boolean)
    .map((match) => Number(match[1]));
  if (matched.length > 1) {
    return { tag: null, hasMultiple: true };
  }
  return { tag: matched.length === 1 ? matched[0] : null, hasMultiple: false };
}

function computeAllPairsLongestPaths(names, callGraph) {
  const dist = new Map();
  const stack = [];
  for (const src of names) {
    const callees = [...(callGraph.get(src) ?? [])].filter((dst) => dst !== src);
    stack.push(...callees.map((dst) => ({ src, current: dst, length: 1, visited: new Set([src, dst]) })));
  }
  while (stack.length > 0) {
    const { src, current, length, visited } = stack.pop();
    const key = `${src}->${current}`;
    if (length > (dist.get(key) ?? 0)) {
      dist.set(key, length);
    }
    const nextNodes = [...(callGraph.get(current) ?? [])].filter(
      (nxt) => !visited.has(nxt) && length + 1 > (dist.get(`${src}->${nxt}`) ?? 0),
    );
    for (const nxt of nextNodes) {
      stack.push({ src, current: nxt, length: length + 1, visited: new Set([...visited, nxt]) });
    }
  }
  return dist;
}

function computeTierBounds(names, distMap, declaredTags) {
  const dMaxMap = new Map();
  const heightMap = new Map();
  for (const name of names) {
    const incoming = names.map((src) => distMap.get(`${src}->${name}`) ?? 0);
    const outgoing = names.map((dst) => distMap.get(`${name}->${dst}`) ?? 0);
    dMaxMap.set(name, Math.max(0, ...incoming));
    heightMap.set(name, Math.max(0, ...outgoing));
  }
  const l0Adapters = new Set(
    names.filter(
      (name) =>
        heightMap.get(name) === 0 &&
        dMaxMap.get(name) === 1 &&
        names
          .filter((caller) => distMap.get(`${caller}->${name}`) === 1)
          .every((caller) => dMaxMap.get(caller) === 0 && (declaredTags.get(caller) === 0 || declaredTags.get(caller) === null)),
    ),
  );
  const bounds = new Map();
  for (const name of names) {
    const isAdapter = l0Adapters.has(name);
    const lb0 = isAdapter ? 0 : dMaxMap.get(name);
    const ub0 = 3 - heightMap.get(name);
    const ancLbs = names
      .filter((anc) => anc !== name && declaredTags.get(anc) !== null && distMap.has(`${anc}->${name}`))
      .filter((anc) => !(distMap.get(`${anc}->${name}`) === 1 && declaredTags.get(anc) === 0 && isAdapter))
      .map((anc) => declaredTags.get(anc) + distMap.get(`${anc}->${name}`));
    const descUbs = names
      .filter((desc) => desc !== name && declaredTags.get(desc) !== null && distMap.has(`${name}->${desc}`))
      .map((desc) =>
        distMap.get(`${name}->${desc}`) === 1 && declaredTags.get(desc) === 0 && l0Adapters.has(desc)
          ? 0
          : declaredTags.get(desc) - distMap.get(`${name}->${desc}`),
      );
    const lb = Math.max(lb0, ...ancLbs);
    const ub = Math.min(ub0, ...descUbs);
    const validTiers = [0, 1, 2, 3].filter((tier) => lb <= tier && tier <= ub).map((tier) => `L${tier}`);
    bounds.set(name, { dMax: dMaxMap.get(name), height: heightMap.get(name), lb0, ub0, isAdapter, validTiers });
  }
  return bounds;
}

const isEntryPoint =
  process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url));
if (isEntryPoint) {
  main().then((code) => process.exit(code));
}
