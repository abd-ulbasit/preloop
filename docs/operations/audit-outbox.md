# Durable audit delivery and recovery

Audit collection, its query API and the console ship in every edition. By
default, asynchronous audit writes use a bounded best-effort worker. Set
`PRELOOP_AUDIT_OUTBOX_ENABLED=true` on every API/worker process and restart
those processes to enable durable delivery. Apply the database migrations
(`alembic upgrade head` from `backend/preloop/models`) before enabling it.
False or an absent flag keeps the best-effort path.

With the flag enabled, supported asynchronous writes persist a sanitized intent
before the relay writes the audit row. A committed intent survives a process
restart. The bounded worker still controls admission: work refused before the
intent commits is not durable. Callers using the transactional enqueue helper
commit the intent with their business transaction; asynchronous wrappers enqueue
from their worker session. The flag does not capture activity outside Preloop.

The relay starts with the API lifecycle, polls every second, and processes at
most 100 intents per pass. Delivery deletes the intent in the same transaction
that creates the audit row. Failed delivery retries with exponential backoff
(one second initially, capped at five minutes). After 20 unsuccessful attempts
the intent is parked as `failed`; unsupported operations park immediately.
Parked intents remain stored and are not retried automatically.

Monitor `preloop.audit.outbox.lag_seconds` (age of due pending work) and
`preloop.audit.outbox.attempts_failed`. A failed intent is excluded from pending
lag; a low lag does not mean there are no parked intents. Intent payloads are
redacted before persistence, but still contain audit metadata and should receive
the same database access controls and backup policy as audit records.

## Replay a bounded batch

Run the local operator command in the backend environment or API container,
using that deployment's `DATABASE_URL`. It requires direct deployment/database
access; it is not a tenant-facing API. First fix the delivery cause (for example,
a schema mismatch or database outage) and keep the outbox flag enabled so the
relay can consume requeued work.

Preview one account's oldest 100 parked intents without changing them:

```bash
python -m preloop.services.audit.replay \
  --account-id 11111111-1111-4111-8111-111111111111 --limit 100
```

The default is a dry run; `--dry-run` may also be passed explicitly. The output
is a count only and never prints stored payloads. The batch size must be 1–1000
(default 100), and an account ID is mandatory. After reviewing the count, apply one
batch explicitly:

```bash
python -m preloop.services.audit.replay \
  --account-id 11111111-1111-4111-8111-111111111111 --limit 100 --apply
```

Apply resets only the selected account's oldest failed intents to `pending`,
clears their prior failure and resets their attempt budget. Pending and already
delivered work are untouched. Concurrent operators skip one another's locked
rows, so the applied count can differ from an earlier preview. The command does
not wait for delivery or loop over further batches. Check delivery and failure
metrics before explicitly replaying another batch. An unfixed failure will park
again after its finite retry budget; do not schedule an automatic replay loop.

Turning the flag off stops the relay and routes subsequent asynchronous writes
back to best effort. It does not discard stored intents; enable the flag again
to resume pending delivery. Delivered intents are deleted, and replay does not
alter historical audit rows.
