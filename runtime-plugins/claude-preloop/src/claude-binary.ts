import { execFile } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { promisify } from "node:util";

import type { ControlConfig } from "./config.js";

const execFileAsync = promisify(execFile);

/**
 * Oldest Claude Code the model upstream accepts for subscription traffic.
 * A turn on an older binary fails with "version X or newer is required".
 * The plugin tests fail when the bundled Claude Code is older than this.
 */
export const MIN_CLAUDE_CODE_VERSION = "2.1.251";

export type ClaudeExecutable = {
  /** Absolute path passed as pathToClaudeCodeExecutable; unset = bundled. */
  path?: string;
  version?: string;
  source: "config" | "path" | "bundled";
};

/** Compare dotted numeric versions; missing parts count as 0. */
export function compareVersions(a: string, b: string): number {
  const left = a.split(".").map((part) => Number.parseInt(part, 10) || 0);
  const right = b.split(".").map((part) => Number.parseInt(part, 10) || 0);
  for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
    const diff = (left[i] ?? 0) - (right[i] ?? 0);
    if (diff !== 0) {
      return diff < 0 ? -1 : 1;
    }
  }
  return 0;
}

/** Claude Code version shipped with the installed Agent SDK. */
export function bundledClaudeCodeVersion(): string | undefined {
  try {
    const require = createRequire(import.meta.url);
    const entry = require.resolve("@anthropic-ai/claude-agent-sdk");
    const pkg = JSON.parse(
      fs.readFileSync(path.join(path.dirname(entry), "package.json"), "utf8"),
    ) as { claudeCodeVersion?: unknown };
    return typeof pkg.claudeCodeVersion === "string"
      ? pkg.claudeCodeVersion
      : undefined;
  } catch {
    return undefined;
  }
}

/** `claude --version` of an executable, e.g. "2.1.300". */
export async function probeClaudeVersion(
  executable: string,
): Promise<string | undefined> {
  try {
    const { stdout } = await execFileAsync(executable, ["--version"], {
      timeout: 5_000,
    });
    return /(\d+\.\d+\.\d+)/.exec(String(stdout))?.[1];
  } catch {
    return undefined;
  }
}

/** First `claude` executable on PATH. */
export function findOnPath(
  command: string,
  searchPath: string = process.env.PATH ?? "",
): string | undefined {
  for (const dir of searchPath.split(path.delimiter)) {
    if (!dir || !path.isAbsolute(dir)) {
      continue;
    }
    const candidate = path.join(dir, command);
    try {
      fs.accessSync(candidate, fs.constants.X_OK);
      if (fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch {
      // keep looking
    }
  }
  return undefined;
}

export type ClaudeExecutableDeps = {
  findClaude?: () => string | undefined;
  probe?: (executable: string) => Promise<string | undefined>;
  bundledVersion?: () => string | undefined;
};

/**
 * The Claude Code binary owned sessions run.
 *
 * `claude_executable` from the control config wins. Otherwise the `claude`
 * on PATH is used when it is newer than the SDK's bundled Claude Code, so a
 * host that keeps Claude Code current is not held back by an older plugin
 * install. Else the bundled binary runs.
 */
export async function resolveClaudeExecutable(
  config: ControlConfig,
  deps: ClaudeExecutableDeps = {},
): Promise<ClaudeExecutable> {
  const probe = deps.probe ?? probeClaudeVersion;
  const bundled = (deps.bundledVersion ?? bundledClaudeCodeVersion)();
  if (config.claude_executable) {
    return {
      path: config.claude_executable,
      version: await probe(config.claude_executable),
      source: "config",
    };
  }
  const onPath = (deps.findClaude ?? (() => findOnPath("claude")))();
  if (onPath) {
    const version = await probe(onPath);
    if (version && (!bundled || compareVersions(version, bundled) > 0)) {
      return { path: onPath, version, source: "path" };
    }
  }
  return { version: bundled, source: "bundled" };
}
