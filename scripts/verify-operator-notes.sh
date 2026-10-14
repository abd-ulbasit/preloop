#!/usr/bin/env bash
# Credential-free note/receipt conformance. Never uses the application's DB.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mode="${1:---cli}"
if [[ "$mode" != --cli && "$mode" != --backend && "$mode" != --all ]]; then
    echo 'Usage: scripts/verify-operator-notes.sh [--cli|--backend|--all]' >&2
    exit 2
fi
export PRELOOP_DISABLE_TELEMETRY=true
export TESTING=true
export DISABLE_RBAC=true

if [[ "$mode" != --backend ]]; then
    # Individual hook fixtures replace HOME with temporary homes. Retain Go's
    # build cache; neither onboarding nor a real provider process is invoked.
    (cd "$repo_root/cli" && go test -count=1 -v ./internal/cmd \
        -run 'Test(OperatorNote|Receipt|ChildrenAllWait|StopHook|StopBlock|SessionEndHook|SessionHookDelivers|NoteLifecycle|InstallApprovalHooksInstallsNoteLifecycle|AgentCapabilitiesRegistry|HarnessKindsAndCapabilities|PermissionAndOperatorNotesSessionIdentityParity)')
fi

if [[ "$mode" != --cli ]]; then
    # An explicit interpreter permits use from worktrees without reusing the
    # editable installation's source path. Prefer this checkout's backend.
    python_bin="${PRELOOP_TEST_PYTHON:-python}"
    for tool in initdb pg_ctl createdb; do
        command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool" >&2; exit 2; }
    done
    "$python_bin" -c 'import alembic, pytest, psycopg' || {
        echo 'Use a development venv, or set PRELOOP_TEST_PYTHON to its Python.' >&2
        exit 2
    }
    # libpq environment can override an explicit host (notably PGHOSTADDR).
    unset PGHOST PGHOSTADDR PGPORT PGDATABASE PGUSER PGPASSWORD PGPASSFILE \
        PGSERVICE PGSERVICEFILE PGOPTIONS PGREQUIREAUTH PGCHANNELBINDING \
        PGSSLMODE PGSSLROOTCERT PGSSLCERT PGSSLKEY PGSSLCRL PGSSLCRLDIR
    run_dir="$(mktemp -d "/tmp/preloop-note-smoke.XXXXXX")"
    pg_started=false
    cleanup() {
        if [[ "$pg_started" == true ]]; then
            pg_ctl -D "$run_dir/data" -m immediate stop >/dev/null
        fi
        rm -rf "$run_dir"
    }
    trap cleanup EXIT
    # Do not honor inherited DATABASE_URL, PGHOST or PGPORT. Bind only loopback
    # and choose an ephemeral port for this newly initialized cluster.
    test_port="$($python_bin -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')"
    initdb -D "$run_dir/data" -A trust -U postgres >"$run_dir/init.log" 2>&1
    pg_ctl -D "$run_dir/data" -l "$run_dir/server.log" \
        -o "-h 127.0.0.1 -p $test_port -k $run_dir" start >/dev/null
    pg_started=true
    createdb -h 127.0.0.1 -p "$test_port" -U postgres preloop_note_smoke
    export DATABASE_URL="postgresql+psycopg://postgres@127.0.0.1:$test_port/preloop_note_smoke"
    export PYTHONPATH="$repo_root/backend"
    (cd "$repo_root/backend/preloop/models" && "$python_bin" -m alembic upgrade head)
    (cd "$repo_root" && "$python_bin" scripts/init_system_roles.py)
    (cd "$repo_root" && "$python_bin" -m pytest -q \
        backend/tests/endpoints/test_operator_notes_api.py \
        backend/tests/services/test_operator_notes_delivery.py \
        backend/tests/models/crud/test_operator_note_crud.py)
fi

echo 'PASS: local fixture/API verification; real harness model consumption remains a separate live check.'
