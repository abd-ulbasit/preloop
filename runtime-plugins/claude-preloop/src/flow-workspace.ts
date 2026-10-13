import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

import type { ControlConfig } from "./config.js";
import { GitRunner, WorkspaceError, defaultGitRunner } from "./workspace.js";

/**
 * Per-flow working directory for a persistent flow execution.
 *
 * The control plane names a directory (`workspace.mode =
 * "authorized_directory"`). The sidecar only runs there when the host owner
 * listed it with `preloop runner dirs add`, which writes
 * `~/.preloop/runner.json` `authorized_directories`. The same refusals as
 * runner sessions apply: unlisted paths, read_only entries, the filesystem
 * root, the home directory or anything containing it, credential
 * directories, and a dirty git tree unless the flow sets `allow_dirty`.
 */
export const FLOW_WORKSPACE_MODE = "authorized_directory";
export const CLAUDE_HARNESS_ID = "claude_code";

const DIRECTORY_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;

/** Credential directories below the home directory (mirrors the runner). */
const SECRET_HOME_DIRS = [
  ".ssh",
  ".gnupg",
  ".aws",
  ".azure",
  ".kube",
  ".docker",
  ".terraform.d",
  path.join(".config", "gcloud"),
  path.join(".config", "rclone"),
  path.join(".local", "share", "keyrings"),
  path.join("Library", "Keychains"),
  path.join(".config", "gh"),
  path.join(".config", "github-copilot"),
  ".copilot",
  ".claude",
  ".codex",
  ".cursor",
  ".preloop",
];

export type FlowWorkspaceSpec = {
  mode?: string;
  kind?: string;
  id?: string;
  path?: string;
  allow_dirty?: boolean;
};

type AuthorizedDirectory = {
  id?: unknown;
  path?: unknown;
  mode?: unknown;
  harnesses?: unknown;
};

export type FlowWorkspaceOptions = {
  git?: GitRunner;
  homeDir?: string;
};

export function runnerConfigPath(config: ControlConfig): string {
  return (
    config.runner_config_path ?? path.join(os.homedir(), ".preloop", "runner.json")
  );
}

async function readAuthorizedDirectories(
  file: string,
): Promise<AuthorizedDirectory[]> {
  let raw: string;
  try {
    raw = await fs.readFile(file, "utf8");
  } catch {
    throw new WorkspaceError(
      `no authorized directories on this host (${file} is missing); add one with: preloop runner dirs add <path>`,
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new WorkspaceError(`authorized directories cannot be read from ${file}`);
  }
  const entries = (parsed as { authorized_directories?: unknown })
    ?.authorized_directories;
  return Array.isArray(entries) ? (entries as AuthorizedDirectory[]) : [];
}

async function realDirectory(value: string): Promise<string | undefined> {
  try {
    const resolved = await fs.realpath(value);
    const stat = await fs.stat(resolved);
    return stat.isDirectory() ? resolved : undefined;
  } catch {
    return undefined;
  }
}

function within(child: string, base: string): boolean {
  const relative = path.relative(base, child);
  return (
    relative === "" ||
    (!relative.startsWith("..") && !path.isAbsolute(relative))
  );
}

function harnessAllowed(harnesses: unknown): boolean {
  if (harnesses === undefined || harnesses === null || harnesses === "all") {
    return true;
  }
  if (Array.isArray(harnesses)) {
    return harnesses.length === 0 || harnesses.includes(CLAUDE_HARNESS_ID);
  }
  return false;
}

async function refuseUnsafeDirectory(dir: string, homeDir: string): Promise<void> {
  if (path.dirname(dir) === dir) {
    throw new WorkspaceError("the filesystem root cannot be a workspace");
  }
  const home = (await realDirectory(homeDir)) ?? path.resolve(homeDir);
  if (within(home, dir)) {
    throw new WorkspaceError(
      "the home directory, or a directory that contains it, cannot be a workspace",
    );
  }
  for (const rel of SECRET_HOME_DIRS) {
    const secretPath = path.join(home, rel);
    const secret = (await realDirectory(secretPath)) ?? secretPath;
    if (within(dir, secret) || within(secret, dir)) {
      throw new WorkspaceError(
        `~/${rel} holds credentials and cannot be a workspace or contain one`,
      );
    }
  }
}

async function hasGitMarker(dir: string): Promise<boolean> {
  let current = dir;
  for (let i = 0; i < 64; i += 1) {
    try {
      await fs.lstat(path.join(current, ".git"));
      return true;
    } catch {
      // keep walking up
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return false;
    }
    current = parent;
  }
  return false;
}

async function isDirty(dir: string, git: GitRunner): Promise<boolean> {
  let result;
  try {
    result = await git(
      ["status", "--porcelain", "--untracked-files=normal"],
      { cwd: dir, timeoutMs: 30_000 },
    );
  } catch {
    throw new WorkspaceError(
      `git status failed in ${dir}, so uncommitted changes cannot be ruled out`,
    );
  }
  if (result.code !== 0) {
    if (!(await hasGitMarker(dir))) {
      return false;
    }
    throw new WorkspaceError(
      `git status failed in ${dir}, so uncommitted changes cannot be ruled out`,
    );
  }
  return result.stdout.trim().length > 0;
}

/**
 * Resolve a flow's host directory to the cwd the session runs in.
 *
 * @throws WorkspaceError when the directory is not authorized or not usable.
 */
export async function resolveFlowWorkspace(
  spec: FlowWorkspaceSpec,
  config: ControlConfig,
  options: FlowWorkspaceOptions = {},
): Promise<string> {
  const file = runnerConfigPath(config);
  const entries = await readAuthorizedDirectories(file);
  let entry: AuthorizedDirectory | undefined;
  let label: string;
  if (spec.kind === "authorized_directory") {
    if (typeof spec.id !== "string" || !DIRECTORY_ID.test(spec.id)) {
      throw new WorkspaceError("authorized directory id is invalid");
    }
    const matches = entries.filter((candidate) => candidate.id === spec.id);
    if (matches.length > 1) {
      throw new WorkspaceError(
        `authorized directory ${spec.id} is listed more than once; remove the duplicate`,
      );
    }
    entry = matches[0];
    label = spec.id;
  } else if (spec.kind === "path") {
    if (typeof spec.path !== "string" || !path.isAbsolute(spec.path)) {
      throw new WorkspaceError("workspace path must be absolute");
    }
    const wanted = await realDirectory(spec.path);
    label = spec.path;
    if (wanted) {
      for (const candidate of entries) {
        if (typeof candidate.path !== "string") {
          continue;
        }
        if ((await realDirectory(candidate.path)) === wanted) {
          entry = candidate;
          break;
        }
      }
    }
  } else {
    throw new WorkspaceError(
      `workspace kind ${JSON.stringify(spec.kind)} is not supported`,
    );
  }
  if (!entry || typeof entry.path !== "string") {
    throw new WorkspaceError(
      `${label} is not an authorized directory on this host; add it with: preloop runner dirs add <path>`,
    );
  }
  if (entry.mode !== "write") {
    throw new WorkspaceError(
      `authorized directory ${label} is not writable (mode ${String(entry.mode)})`,
    );
  }
  if (!harnessAllowed(entry.harnesses)) {
    throw new WorkspaceError(
      `authorized directory ${label} does not allow harness ${CLAUDE_HARNESS_ID}`,
    );
  }
  const dir = await realDirectory(entry.path);
  if (!dir) {
    throw new WorkspaceError(`authorized directory ${label} does not exist`);
  }
  // `preloop runner dirs add` stores the realpath. A stored path that now
  // resolves elsewhere was replaced by a link or moved after it was
  // authorized, so the authorization no longer applies (same rule as the
  // runner).
  if (dir !== path.resolve(entry.path)) {
    throw new WorkspaceError(
      `authorized directory ${label} now resolves to ${dir} (a symlink or moved directory); remove the entry and add that path`,
    );
  }
  await refuseUnsafeDirectory(dir, options.homeDir ?? os.homedir());
  const dirty = await isDirty(dir, options.git ?? defaultGitRunner);
  if (dirty && spec.allow_dirty !== true) {
    throw new WorkspaceError(
      `authorized directory ${label} has uncommitted changes; commit or stash them, or set agent_config.workspace.allow_dirty`,
    );
  }
  return dir;
}

/** Upper bound on a flow-requested turn, whatever the flow asks for. */
export const MAX_FLOW_TURN_TIMEOUT_MS = 24 * 60 * 60 * 1000;

function positiveSeconds(value: unknown): number | undefined {
  const number = typeof value === "string" ? Number(value) : value;
  if (typeof number === "number" && Number.isFinite(number) && number > 0) {
    return number;
  }
  return undefined;
}

/**
 * Turn timeout for one message.
 *
 * A flow's `run_limits.max_duration_seconds` (or `timeout_seconds`) sets the
 * turn timeout. `turn_timeout_ms` from the control config bounds it from
 * above when set. Without a flow value the config value, or the default,
 * applies.
 */
export function selectTurnTimeoutMs(
  config: ControlConfig,
  metadata: Record<string, unknown> | undefined,
  defaultMs: number,
): number {
  const configured =
    typeof config.turn_timeout_ms === "number" &&
    Number.isFinite(config.turn_timeout_ms) &&
    config.turn_timeout_ms > 0
      ? config.turn_timeout_ms
      : undefined;
  const limits = metadata?.["run_limits"];
  const seconds =
    positiveSeconds(
      limits && typeof limits === "object"
        ? (limits as Record<string, unknown>)["max_duration_seconds"]
        : undefined,
    ) ?? positiveSeconds(metadata?.["timeout_seconds"]);
  if (seconds === undefined) {
    return configured ?? defaultMs;
  }
  const requested = Math.min(seconds * 1000, MAX_FLOW_TURN_TIMEOUT_MS);
  return configured !== undefined ? Math.min(requested, configured) : requested;
}

const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._:/@[\]-]{0,127}$/;

/** The flow's model name from send_message metadata, when well formed. */
export function flowModel(
  metadata: Record<string, unknown> | undefined,
): string | undefined {
  const value = metadata?.["model"];
  if (typeof value !== "string") {
    return undefined;
  }
  const trimmed = value.trim();
  return MODEL_NAME.test(trimmed) ? trimmed : undefined;
}
