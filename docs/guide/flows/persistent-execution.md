# Persistent flow execution

Editions: OSS, Cloud, Enterprise. Unless stated otherwise, everything on this page ships in OSS.

Use **Persistent (Govern persistent agent node)** when the work should run on
an already-enrolled managed agent that is connected to Agent Control, instead
of provisioning a short-lived container.

Typical reasons to pick it:

* The agent already has the repository, tools, or session context on its host.
* You want the run governed through the same Agent Control audit trail as
  console and mobile operator messages.
* An ephemeral clone would be slower or would not see local state.

## Fail-fast rule

Start does **not** fall back to an ephemeral container. The execution fails
immediately when:

* the flow has no `target_agent_id`
* the target is missing, inactive, or not an Agent Control kind
* the target only accepts text on an already-open session (currently Pi
  and DeepSeek); persistent start always opens a new session
* Agent Control is not verified on that agent
* the agent's control heartbeat is stale (it is offline)

The flow form shows each target's Agent Control state and disables agents that
are not online. If you save an offline target, start still fails until that
agent reconnects. Connected Pi and DeepSeek targets can still appear in the
picker; start refuses them because they cannot open a new session.

## What happens at run time

1. The orchestrator renders the flow prompt the same way as the ephemeral path.
2. Preloop persists one `send_message` command, then delivers it to the target.
3. The execution stays `RUNNING` while the command is pending, delivered, or
   acked without a result.
4. A successful `command_result` marks the execution succeeded. A
   `command_error`, expiry, or stop marks it failed.
5. If the flow timeout budget expires, Preloop interrupts the agent's
   **current** session (`session_mode: current`) and stops waiting. That is
   usually this flow's turn; it is not a guarantee if another session started.

See [Flow execution on a persistent agent](../../architecture/agent-control.md#flow-execution-on-a-persistent-agent)
for the envelope, binding, and status table.

## Checkout and clone-less

Persistent execution sends a `workspace` object on the `send_message`.

* **Checkout** (`workspace.mode` is `persistent_checkout`): the flow has git
  clone enabled and the trigger names a repository. The sidecar keeps
  `<workspace_root>/<repository_slug>` on the agent host, clones it once
  with the host's own git credentials, then fetches and checks out later
  runs. No tracker token is sent to the sidecar.
* **Clone-less** (`workspace.mode` is `clone_less`): git clone is disabled,
  no repository could be resolved, or the flow lists more than one
  repository. The preset must not run git. The pull request reviewer
  reads the diff from the tracker and says so in the review.
* **Ephemeral** runs are unchanged. Their prompt renders
  `workspace.mode` as `ephemeral` and the container still clones into
  its own workspace.

Presets declare `supports_persistent`. The marker means the prompt was
checked against persistent modes. It does not mean the host captures
container result files. When Persistent is selected, the flow form
disables presets that do not support it. The pull request reviewer
supports it. Presets that write a container result path, or that still
assume an ephemeral checkout, do not.

## Run in a directory on the agent host

On a Claude Code target, a flow can name the directory the session runs in
with `agent_config.workspace`. Other targets (for example Codex) do not
support it yet, and start fails instead of running in their default
directory.

```json
{
  "execution_path": "persistent",
  "target_agent_id": "<managed agent id>",
  "workspace": {"kind": "authorized_directory", "id": "dir_..."}
}
```

The host owner authorizes the directory first:

```bash
preloop runner dirs add ~/code/project --mode write --harness claude_code
preloop runner dirs list   # shows the dir_... id
```

`{"kind": "path", "path": "/abs/path"}` is also accepted, but only when that
path is an authorized directory on the host. Preloop validates the shape when
the flow is saved; the Claude sidecar resolves it at run time against
`~/.preloop/runner.json` and fails the execution when:

* the id or path is not in the authorized list, or the entry is `read_only`
  or limited to other harnesses
* the directory is the filesystem root, the home directory (or contains it),
  or a credential directory such as `~/.ssh`, `~/.claude` or `~/.preloop`
* the directory has uncommitted changes, unless the flow sets
  `"allow_dirty": true`

A named directory replaces the checkout for that run: git clone settings are
not used. Without `workspace`, the session runs in `workspace_root` from the
sidecar config, as before. The sidecar ignores a raw `cwd` in the message, so
a message cannot point a session at a directory the host owner did not list.

## Model

When the flow's model is a Claude model (Anthropic provider, or an identifier
containing `claude`), Preloop sends its name as `metadata.model`: the gateway
alias for gateway-routed models, else the model identifier. The Claude sidecar
starts the session with that model. Other models are not sent, and the host's
own Claude Code setting applies.

## Turn timeout

The sidecar waits for one turn as long as the flow timeout
(`run_limits.max_duration_seconds`, else `timeout_seconds`), up to 24 hours.
`turn_timeout_ms` in `~/.claude/preloop-control.json`, when set, is an upper
bound. With neither, the default stays 5 minutes.

## Unattended scheduled runs

A scheduled persistent run has nobody at the terminal. Every tool call still
goes through the Preloop PreToolUse hook installed by
`preloop agents onboard "Claude Code" --approvals`, so a call that escalates
to a human waits for an approver and fails when the approval expires.

Give the flow an approval policy so the calls it needs are approved for that
run only:

```json
{
  "execution_path": "persistent",
  "target_agent_id": "<managed agent id>",
  "workspace": {"kind": "authorized_directory", "id": "dir_..."},
  "approval_policy": {
    "mode": "auto_allow",
    "tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
    "paths": ["<workspace>"],
    "commands": ["git status", "git diff", "git add", "git commit", "pytest"]
  }
}
```

* `mode` is `ask` (the default: nothing changes) or `auto_allow`.
* `tools` may list `Bash`, `Read`, `Edit`, `MultiEdit`, `Write`, `Glob`,
  `Grep`, `LS`, `NotebookEdit` and `TodoWrite`. Network tools and MCP tools
  are not eligible.
* `paths` are absolute directories on the agent host. `<workspace>` is the
  flow's `workspace.path` when it names one, else the directory the session
  runs in.
* `commands` is required when `tools` lists `Bash`: the command prefixes
  (whole words) a Bash call may start with. Shells, interpreters and command
  runners (`sh`, `bash`, `python`, `node`, `env`, `xargs`, `eval`, `find`,
  `awk` and similar) cannot be listed, and tools such as `git`, `npm` or
  `docker` must be listed with their subcommand (`git commit`, not `git`).
  git calls with options that run other programs or move its config
  (`-c`, `-C`, `--exec-path`, `--upload-pack`, `--git-dir` and similar) are
  not covered.

How the grant is scoped:

* The sidecar starts the flow's Claude Code session with
  `PRELOOP_FLOW_EXECUTION_ID`, and the hook sends it with each permission
  check. The grant only exists while that execution is active, only for the
  managed agent the execution was dispatched to, and only for the first
  native session that presents the execution id. Interactive sessions are
  not affected.
* A covered call is still recorded as an approval request, resolved as
  auto-approved by the flow's approval policy, so the audit trail shows it.
* Not covered, so it takes the normal approval path: file paths outside
  `paths`; a Bash call whose working directory is outside `paths`; a Bash
  command with any word that does not start with a listed prefix; a command
  with `$`, backticks, backslashes, newlines, redirections, subshells or
  background jobs; a word that names a path outside `paths`, or uses `~` or
  `..`; `git push`, `fetch`, `pull`, `clone` or remote changes, network tools
  such as `curl`, `wget`, `ssh` and `gh`, publishing commands, and `sudo`,
  even when quoted. Deny rules and require-approval rules in your approval
  policies still win.

What the checks do not cover, so scope the policy accordingly:

* The checks read the command and paths as written. The control plane
  cannot resolve symlinks on the host, so a symlink inside `paths` that
  points elsewhere is not detected.
* A listed command can run code from the workspace: `pytest` runs test
  files and `conftest.py`, `npm test` runs package scripts, and the session
  can edit those files. Auto-approving such a command gives the session the
  host user's rights for whatever that code does. List only what the run
  needs.

Also recommended:

* Set `"permission_mode": "acceptEdits"` in the sidecar control config, so
  file edits do not wait on Claude Code's own prompt, which nobody can answer
  in an owned session. Do not use `bypassPermissions`.
* Point the flow at an authorized directory (above) and keep the tree clean,
  or set `allow_dirty` deliberately.
* Set the flow timeout to cover the whole run.

## What this does not do yet

Persistent mode does not forward git credentials. The Claude sidecar and
the Codex sidecar both implement this workspace checkout contract. Codex
is already on the Agent Control allow-list.
