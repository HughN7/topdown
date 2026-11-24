import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

// [L0] Hook Shim Entrypoint
function main() {
  const rawInput = fs.readFileSync(0);
  const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
  const hookScript = path.join(repoRoot, "enforcement", "run_hook.py");
  const runner = findPythonRunner(repoRoot);
  if (!runner) {
    process.stdout.write("{}\n");
    process.exit(0);
  }
  const proc = spawnSync(runner.bin, [...runner.args, hookScript], {
    input: rawInput,
    cwd: repoRoot,
    stdio: ["pipe", "inherit", "inherit"],
  });
  process.exit(proc.status ?? 0);
}

// [L1] Locate Runnable Python
function findPythonRunner(repoRoot) {
  for (const candidate of collectPythonCandidates(repoRoot)) {
    const probe = spawnSync(candidate.bin, [...candidate.args, "--version"], {
      stdio: "ignore",
      timeout: 2000,
    });
    if (probe.status === 0) {
      return candidate;
    }
  }
  return null;
}

// [L3] Enumerate Python Candidates
function collectPythonCandidates(repoRoot) {
  const candidates = [];
  for (const relPath of [path.join(".venv", "Scripts", "python.exe"), path.join(".venv", "bin", "python")]) {
    const venvBin = path.join(repoRoot, relPath);
    if (fs.existsSync(venvBin)) {
      candidates.push({ bin: venvBin, args: [] });
    }
  }
  candidates.push({ bin: "python3", args: [] }, { bin: "python", args: [] }, { bin: "py", args: ["-3"] });
  const localAppData = process.env.LOCALAPPDATA;
  if (!localAppData) {
    return candidates;
  }
  const pyRoot = path.join(localAppData, "Programs", "Python");
  if (!fs.existsSync(pyRoot)) {
    return candidates;
  }
  const dirs = fs.readdirSync(pyRoot).filter((entry) => /^Python\d+$/i.test(entry)).sort().reverse();
  for (const dirName of dirs) {
    const exePath = path.join(pyRoot, dirName, "python.exe");
    if (fs.existsSync(exePath)) {
      candidates.push({ bin: exePath, args: [] });
    }
  }
  return candidates;
}

main();
