/**
 * The Overview "Failures and denials" card: which audit groups it lists, what
 * each row says, and how repeats fold into one row.
 *
 * A grouped audit row carries a chain (the tool call, the policy decision,
 * approval steps) and the server picks one outcome to show. Before core
 * reported a failed call's own status, a call the policy allowed and that then
 * failed came back as `outcome: "allow"`, and the card printed "allow" in red.
 * The card therefore decides the reason itself, from the whole chain, and
 * drops a row it cannot name rather than showing an allowed call as a failure.
 */

export interface ExceptionAuditEvent {
  id: string;
  action: string;
  status: string;
  timestamp: string;
  details: Record<string, unknown> | null;
}

export interface ExceptionAuditGroup {
  correlation_id: string | null;
  primary_event: ExceptionAuditEvent;
  sub_events: ExceptionAuditEvent[];
  outcome: string;
}

/**
 * The outcomes the card lists. A gateway `error` is not here: the Gateway
 * failures card next to this one already lists those requests.
 */
export const EXCEPTION_OUTCOMES = [
  'failed',
  'upstream_error',
  'deny',
  'budget_denied',
] as const;

export type ExceptionReason = (typeof EXCEPTION_OUTCOMES)[number];

const REASONS: Record<
  ExceptionReason,
  { label: string; variant: 'danger' | 'warning' }
> = {
  failed: { label: 'Failed', variant: 'danger' },
  upstream_error: { label: 'Upstream error', variant: 'danger' },
  deny: { label: 'Denied by policy', variant: 'warning' },
  budget_denied: { label: 'Budget denied', variant: 'warning' },
};

const isReason = (value: unknown): value is ExceptionReason =>
  typeof value === 'string' &&
  (EXCEPTION_OUTCOMES as readonly string[]).includes(value);

const text = (value: unknown): string =>
  typeof value === 'string' ? value.trim() : '';

/** Why this group is on the card, or null when it is not a failure or denial. */
export function exceptionReason(
  group: ExceptionAuditGroup
): ExceptionReason | null {
  // What the call itself did comes first: a failed call is "failed" even if
  // the chain also says the policy allowed it.
  if (isReason(group.primary_event.status)) return group.primary_event.status;
  if (isReason(group.outcome)) return group.outcome;
  const decision = [group.primary_event, ...group.sub_events].find(
    (event) => text(event.details?.decision) === 'deny'
  );
  return decision ? 'deny' : null;
}

export function exceptionReasonLabel(reason: ExceptionReason): string {
  return REASONS[reason].label;
}

export function exceptionReasonVariant(
  reason: ExceptionReason
): 'danger' | 'warning' {
  return REASONS[reason].variant;
}

/** Who made the call: the agent or session name, else the key. */
export function exceptionAgent(group: ExceptionAuditGroup): string {
  for (const event of [group.primary_event, ...group.sub_events]) {
    const details = event.details || {};
    const name =
      text(details.runtime_principal_name) ||
      text(details.agent_name) ||
      text(details.api_key_name);
    if (name) return name;
  }
  return 'Unknown agent';
}

/** The tool or the model the call was for. */
export function exceptionSubject(group: ExceptionAuditGroup): string {
  const details = group.primary_event.details || {};
  return (
    text(details.tool_name) ||
    text(details.requested_model) ||
    text(details.model_alias) ||
    (group.primary_event.action === 'model_gateway_request'
      ? 'Model request'
      : 'Tool call')
  );
}

/** The error, or the rule that blocked the call, in one line. */
export function exceptionDetail(group: ExceptionAuditGroup): string {
  const events = [group.primary_event, ...group.sub_events];
  for (const event of events) {
    const details = event.details || {};
    const code = text(details.error_code);
    const reason = text(details.error_reason);
    const message =
      code && reason
        ? `${code}: ${reason}`
        : reason ||
          code ||
          text(details.error_detail) ||
          text(details.error) ||
          text(details.rule_description) ||
          text(details.rule_name);
    if (message) return message.replace(/\s+/g, ' ');
  }
  return '';
}

export interface ExceptionRow {
  /** The newest group of the run: the row opens this event. */
  group: ExceptionAuditGroup;
  reason: ExceptionReason;
  agent: string;
  subject: string;
  detail: string;
  /** How many groups said the same thing. */
  count: number;
}

/**
 * Rows for the card, newest first. Groups with the same reason, agent,
 * tool or model and message fold into one row with a count; a group that is
 * not a failure or denial is dropped.
 */
export function exceptionRows(groups: ExceptionAuditGroup[]): ExceptionRow[] {
  const rows = new Map<string, ExceptionRow>();
  const sorted = [...groups].sort((a, b) =>
    b.primary_event.timestamp.localeCompare(a.primary_event.timestamp)
  );
  for (const group of sorted) {
    const reason = exceptionReason(group);
    if (!reason) continue;
    const agent = exceptionAgent(group);
    const subject = exceptionSubject(group);
    const detail = exceptionDetail(group);
    const key = JSON.stringify([reason, agent, subject, detail]);
    const row = rows.get(key);
    if (row) {
      row.count += 1;
    } else {
      rows.set(key, { group, reason, agent, subject, detail, count: 1 });
    }
  }
  return [...rows.values()];
}

/** The Audit page, filtered to what the card lists, from `startDate` on. */
export function exceptionsAuditHref(startDate: string | null): string {
  const params = new URLSearchParams();
  for (const outcome of EXCEPTION_OUTCOMES) params.append('outcome', outcome);
  params.set('outcome_match', 'any');
  // The Audit filter is a calendar day: the reader's day the range starts on.
  const start = startDate ? new Date(startDate) : null;
  if (start && !Number.isNaN(start.getTime())) {
    const pad = (n: number) => String(n).padStart(2, '0');
    params.set(
      'start_date',
      `${start.getFullYear()}-${pad(start.getMonth() + 1)}-${pad(start.getDate())}`
    );
  }
  return `/console/audit?${params}`;
}

/**
 * The Audit page at one event. It keeps the card's filters, so the event is
 * on the first page of what the timeline loads and is opened there.
 */
export function exceptionEventHref(
  group: ExceptionAuditGroup,
  startDate: string | null
): string {
  const href = exceptionsAuditHref(startDate);
  return `${href}&event=${encodeURIComponent(group.primary_event.id)}`;
}
