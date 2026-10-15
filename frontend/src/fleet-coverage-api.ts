import { fetchWithAuth } from './api';

export interface FleetApplication {
  agent_kind: string;
  app_version: string | null;
  profile_ref?: string | null;
  installed: string;
  configured: string;
  observed_active: string;
  controls: Array<{
    axis: string;
    requested: string;
    applied: string;
    supported: string;
    method: string;
  }>;
}
export interface FleetSource {
  id: string;
  source_ref: string;
  received_at: string;
  freshness: string;
  application_count: number;
  device_binding: string;
  gaps: string[];
  evidence: {
    schema_version: number;
    collector_version: string | null;
    detector_version: string | null;
    started_at: string;
    ended_at: string;
    scan_scope: string[];
    completeness: string;
    errors: string[];
    profiles?: Array<{
      profile_ref: string;
      completeness: string;
      error_code: string | null;
    }>;
    applications?: FleetApplication[];
  };
}
export interface FleetDevice {
  device_ref_hash: string;
  platform?: string;
  states: string[];
  last_reported_at?: string | null;
  sources: FleetSource[];
}
export interface FleetCoverage {
  schema_version: number;
  items: FleetDevice[];
  total: number;
  unmatched_reports: FleetDevice[];
  observation_sources_total: number;
  truncated: boolean;
  freshness_seconds: number;
  summary: {
    expected: number;
    reporting_fresh: number;
    complete_fresh: number;
    verified_compliant: number;
    unknown_compliance: number;
    reporting_ratio: number | null;
    verified_compliant_ratio: number | null;
  };
}
export interface FleetEvidence {
  items: FleetSource[];
  total: number;
  truncated: boolean;
  freshness_seconds: number;
}
async function read<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetchWithAuth(`/api/v1/fleet/${path}`, { signal });
  if (!response.ok) {
    throw new Error(
      response.status === 401 || response.status === 403
        ? 'Fleet coverage requires an authorized account with permission to view agents.'
        : 'Fleet evidence could not be loaded. Retry to refresh it.'
    );
  }
  return response.json();
}
export function getFleetCoverage(
  seconds: number,
  signal: AbortSignal
): Promise<FleetCoverage> {
  return read(`coverage?freshness_seconds=${seconds}`, signal);
}
export function getFleetEvidence(
  fingerprint: string,
  seconds: number,
  signal: AbortSignal
): Promise<FleetEvidence> {
  return read(
    `evidence?freshness_seconds=${seconds}&workstation_fingerprint=${encodeURIComponent(fingerprint)}`,
    signal
  );
}

/** One row per independent source, including devices without retained evidence. */
export function coverageCsv(page: FleetCoverage): string {
  const rows: unknown[][] = [
    [
      'population',
      'device_ref_hash',
      'platform',
      'states',
      'last_reported_at',
      'freshness_seconds',
      'truncated',
      'expected',
      'loaded_devices',
      'source_ref',
      'observation_id',
      'collection_start',
      'collection_end',
      'freshness',
      'scope',
      'completeness',
      'collector_version',
      'detector_version',
      'profiles',
      'applications_found',
      'verification',
      'effective_coverage',
    ],
  ];
  for (const [population, devices] of [
    ['expected', page.items],
    ['unmatched', page.unmatched_reports],
  ] as const) {
    for (const device of devices) {
      for (const source of device.sources.length
        ? device.sources
        : [undefined]) {
        rows.push([
          population,
          device.device_ref_hash,
          device.platform ?? 'unknown',
          device.states.join(';'),
          device.last_reported_at ?? '',
          page.freshness_seconds,
          page.truncated || page.items.length < page.total,
          page.summary.expected,
          page.items.length,
          source?.source_ref ?? '',
          source?.id ?? '',
          source?.evidence.started_at ?? '',
          source?.evidence.ended_at ?? '',
          source?.freshness ?? 'unknown',
          source?.evidence.scan_scope.join(';') ?? 'unknown',
          source?.evidence.completeness ?? 'unknown',
          source?.evidence.collector_version ?? 'unknown',
          source?.evidence.detector_version ?? 'unknown',
          source?.evidence.profiles
            ? JSON.stringify(source.evidence.profiles)
            : 'unknown',
          source?.application_count ?? 'unknown',
          'unverified',
          'unknown',
        ]);
      }
    }
  }
  if (rows.length === 1) {
    rows.push([
      'summary',
      '',
      '',
      '',
      '',
      page.freshness_seconds,
      page.truncated || page.items.length < page.total,
      page.summary.expected,
      page.items.length,
      '',
      '',
      '',
      '',
      'unknown',
      'unknown',
      'unknown',
      'unknown',
      'unknown',
      'unknown',
      'unknown',
      'unverified',
      'unknown',
    ]);
  }
  // Quote every cell and neutralize spreadsheet formulas, including leading whitespace.
  return (
    rows
      .map((row) =>
        row
          .map((value) => {
            let cell = String(value);
            if (/^\s*[=+@-]/.test(cell) || /^[\t\r\n]/.test(cell))
              cell = `'${cell}`;
            return `"${cell.replace(/"/g, '""')}"`;
          })
          .join(',')
      )
      .join('\r\n') + '\r\n'
  );
}
