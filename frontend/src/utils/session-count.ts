import type { ListTable } from '../table';
import type { GatewayUsageBySession } from '../types';

/**
 * "12 sessions", or "12 sessions, 15 rows" when sessions span models: the
 * summary has one row per (session, model), and a row is not a session.
 */
export function sessionCountLabel(
  table: ListTable<GatewayUsageBySession>
): string {
  const rows = table.filteredRowCount;
  const ids = new Set(
    table.filteredRows.map(
      (row) =>
        row.runtime_session_id ??
        `${row.session_source_type ?? ''}:${row.session_source_id ?? ''}`
    )
  );
  const sessions = ids.size;
  const label = `${sessions.toLocaleString()} ${sessions === 1 ? 'session' : 'sessions'}`;
  return rows === sessions ? label : `${label}, ${rows.toLocaleString()} rows`;
}
