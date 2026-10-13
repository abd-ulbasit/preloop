# Verify operator-note delivery

Operator-note receipts need two kinds of evidence: local protocol tests and an
installed harness consuming the carrying output. A `delivered` receipt means
Preloop wrote the carrying response; it does not prove the model read or obeyed
it. Keep those claims separate when reporting a smoke run.

## Local conformance without provider credentials

From the repository root:

```bash
scripts/verify-operator-notes.sh --cli
# With a development Python environment and native PostgreSQL tools:
PRELOOP_TEST_PYTHON=/path/to/venv/bin/python \
  scripts/verify-operator-notes.sh --all
```

`--cli` runs versioned Claude Code, Codex CLI and Cursor CLI hook fixtures,
carrying versus non-carrying output, session-scoped spooling, acknowledgment,
expiry/drop handling, stop caps, session-end reporting and receipt/wait exit
codes. It also checks the supported-harness capability registry. Go tests use
synthetic credentials and temporary homes; they do not run real vendor CLIs.

`--backend` runs the API, CRUD and gateway note suites against an ASGI test
server and a newly initialized PostgreSQL cluster. The script binds PostgreSQL
to loopback on a temporary port, migrates a fresh database, seeds system roles,
then stops and removes the cluster on exit. It overrides `DATABASE_URL` and
`PYTHONPATH`, so an existing application database and editable install's source
path are not used. Seeding matters: the OSS permission fallback requires the
fixture user to hold its owner role. Disabling RBAC alone does not grant that
permission.

Prerequisites are `go` for CLI tests, and `initdb`, `pg_ctl`, `createdb`, plus
Alembic, pytest and psycopg in the development Python environment for backend
tests. PostgreSQL initialization must run as an ordinary user. All modes set
`PRELOOP_DISABLE_TELEMETRY=true`. A passing local run is fixture/API evidence,
not evidence of installed harness behavior.

The gateway tests cover OpenAI Chat Completions, OpenAI Responses and Anthropic
Messages. They verify note insertion into outbound request bodies, account
isolation, expiry, retry behavior and recorded delivery. A harness supported
through a managed gateway receives notes at its next routed model request.
Native permission support alone does not imply a model-visible note carrier;
a hook without one must keep its receipt claimed until a carrying boundary.
Use the current adapter registry and onboarding output to identify the chosen
rail for each supported harness instead of assuming every harness has the
same lifecycle hooks.

## Installed harnesses with simulated providers

```bash
python scripts/smoke_operator_notes_claude.py
# Or test an already built integration branch CLI:
python scripts/smoke_operator_notes_claude.py --preloop /path/to/preloop
python scripts/smoke_operator_notes_codex.py --preloop /path/to/preloop
```

This runs the installed Claude executable with a fresh home, settings and
configuration directory, synthetic credentials and a loopback Anthropic
Messages fixture. It builds Preloop in the temporary directory by default.
No existing vendor configuration, login or provider is reused. The first
outbound model request must contain the SessionStart marker; a note queued by
the fixture after that request must appear in the next request after Stop
continues the worker. The actual executable must emit SessionEnd, allowing the
fixture note API to terminalize a leftover session note. The script reports
harness version, hook events and each assertion, without request bodies or
credentials. Backend conformance separately checks the real API's state
transitions.

Verified on 2026-10-13 with Claude Code 2.1.296: all five checks passed, including
both model-visible markers and SessionEnd. This is installed-harness evidence
with a simulated provider and note API. Real provider access was not used:
none of the provider credential environment variables were present, and a
fresh isolated Claude config reported `loggedIn=false`, `authMethod=none`.
The normal config reported API-key authentication via stored settings; that
configuration and key were not copied into the smoke home.

The Codex script uses an isolated `CODEX_HOME`, `hooks.json` and a local OpenAI
Responses provider. It applies the same five assertions and invokes the real
CLI with an ephemeral read-only session and hook-trust bypass for the synthetic
commands. Verified on 2026-10-13 with Codex CLI 0.162.1: all five checks passed,
two outbound model requests contained the corresponding startup and stop
markers, and the CLI emitted SessionEnd. This establishes installed Codex
lifecycle/config acceptance with simulated provider/API; it does not reuse the
normal Codex login or claim real provider validation.

## Installed-harness smoke

Use a disposable local Preloop instance and a test account. Set
`PRELOOP_DISABLE_TELEMETRY=true` in the instance and every shell. Use a new
`HOME` and a temporary working directory, plus the harness-specific config
root if that harness keeps configuration outside `HOME`. Provision test-only
Preloop and provider credentials there. Do not copy a normal home's credential
files or onboard against its existing settings.

Build the current Preloop CLI with `cd cli && make build`. Record its version,
the server commit, the vendor executable path/version and the delivery rail.
Install onboarding hooks or managed gateway routing using that isolated home
and the test account. A real run requires test provider access; executable
presence and a version command do not establish that access.

For each harness and supported rail, record these cases:

1. **Before the first tool.** Send an agent-addressed note before starting the
   worker. Ask the worker to print a synthetic marker supplied by the note and
   make no tools calls. Verify model-visible context or output contains the
   marker, the receipt becomes delivered once, and the delivery event matches
   the carrying boundary. Harnesses with only PreToolUse require a tool call;
   report that boundary explicitly.
2. **After the last tool.** Arrange a local fixture file as a rendezvous after
   the worker's final tool call. Send a note while the worker approaches stop.
   Verify a supported stop hook resumes the worker once, the marker reaches
   the next model request, and repeated empty stop events do not continue it.
   Report no stop rescue when the installed adapter lacks that boundary.
3. **No carrying boundary left.** Keep a session-addressed note pending or
   claimed, then let the session end normally. Verify it becomes undeliverable
   and an agent-addressed note for a future session stays pending. Repeat with
   an abrupt process exit to check the server's stale-session cleanup rather
   than assuming a vendor emits SessionEnd after a crash.
4. **Non-carrying event.** For a harness with a permission hook that cannot
   carry context, queue a note before that event. Observe claimed until its
   next carrying boundary, then delivered. A different session must not drain
   its spool. Check that a failed carrying write never reports delivered.
5. **Wait results.** Repeat with `preloop notes send --session <id> --wait 30s`.
   Capture exit 0 for delivered, 3 for expired/undeliverable and 4 for a timeout
   while the session remains live. Verify `notes status <note-id> --json` and
   `notes list --session <id> --state claimed --json` agree with the receipt.

Collect synthetic note ids, receipt JSON, hook event names and redacted model
context as evidence. Avoid publishing provider keys, runtime bearer tokens or
normal session transcripts. A vendor CLI pointed at a local protocol fixture
can establish that its parser consumed hook context without a provider call;
label that as an installed harness with a simulated provider, separately from
a real provider run.

The focused follow-up for versioned installed-harness evidence is
[#1637](https://github.com/preloop/preloop/issues/1637). The broader runner
adapter conformance suite is tracked separately in
[#1494](https://github.com/preloop/preloop/issues/1494).
