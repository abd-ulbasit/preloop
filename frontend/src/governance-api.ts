/** Console contract for the governance review plugin, behind governance_review. */
import { extractErrorMessage, fetchWithAuth } from './api';

export type FindingKind = 'violation' | 'inefficiency' | 'anomaly';
export type FindingSeverity = 'high' | 'medium' | 'low';
export type DismissReason = 'expected' | 'fixed' | 'not_applicable';
export interface GovernanceFinding {
  id: string;
  run_id: string;
  kind: FindingKind;
  category: string;
  severity: FindingSeverity;
  title: string;
  rule_ref?: string;
  evidence: {
    excerpt: string;
    source: string;
    audit_event_id?: string;
    api_usage_id?: string;
  }[];
  session_ids: string[];
  user_id?: string;
  user_name?: string;
  agent_ref?: string;
  suggestion: string;
  estimated_weekly_savings?: number;
  dismissed_by_id?: string;
  dismissed_by_name?: string;
  dismissed_at?: string;
  dismissed_reason?: string;
  created_at: string;
}
export interface GovernanceRun {
  id: string;
  window_start: string;
  window_end: string;
  status: string;
  started_at: string;
  finished_at?: string;
  sessions_considered: number;
  sessions_summarized: number;
  segments: number;
  summaries_started_at?: string;
  stop_reason?: string;
  platform_cost?: number;
  account_cost?: number;
  mode_after?: string;
}
export interface FindingFilters {
  kind?: string;
  severity?: string;
  user_id?: string;
  run_id?: string;
  dismissed?: boolean;
  limit?: number;
  offset?: number;
}
const BASE = '/api/v1/governance-review';
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetchWithAuth(`${BASE}${path}`, {
    ...options,
    passive: !options?.method || options.method === 'GET',
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      extractErrorMessage(
        body,
        response.status === 404
          ? 'Finding not found or unavailable.'
          : 'Could not load governance review.'
      )
    );
  }
  return response.json();
}
function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  return `?${search}`;
}
export const getGovernanceRuns = (
  offset = 0,
  run_id?: string
): Promise<GovernanceRun[]> =>
  request(`/runs${query({ limit: 25, offset, run_id })}`);
export const getGovernanceFindings = (
  filters: FindingFilters
): Promise<GovernanceFinding[]> =>
  request(`/findings${query({ limit: 50, ...filters })}`);
export const getGovernanceFinding = (id: string): Promise<GovernanceFinding> =>
  request(`/findings/${encodeURIComponent(id)}`);
export const dismissGovernanceFinding = (
  id: string,
  reason: DismissReason
): Promise<GovernanceFinding> =>
  request(`/findings/${encodeURIComponent(id)}/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason }),
  });
export const restoreGovernanceFinding = (
  id: string
): Promise<GovernanceFinding> =>
  request(`/findings/${encodeURIComponent(id)}/restore`, { method: 'POST' });
