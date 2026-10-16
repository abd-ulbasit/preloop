import { fetchWithAuth, getFeatures, getUserProfile } from './api';
import type { UserProfile } from './api';

export function hasFleetPermission(
  user: Pick<UserProfile, 'permissions' | 'is_account_owner'>,
  permission: string
): boolean {
  return Array.isArray(user.permissions)
    ? user.permissions.includes(permission)
    : user.is_account_owner === true;
}

export interface FleetAgent {
  id: string;
  kind: string;
  status: string;
  managed_agent_id: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
}
export interface DeviceFleetDevice {
  device_key: string;
  hostname: string | null;
  os: string | null;
  runner_state: string;
  collection_state: string;
  inventory_state: string;
  roster_state: string;
  verification: 'unverified';
  last_seen_at: string | null;
  agents_onboarded: number;
  agents_not_onboarded: number;
  users: { id: string; label: string }[];
  runners: { id: string; name: string; state: string }[];
  agents: FleetAgent[];
  sources: Record<string, unknown>[];
  possible_same_machine_as: string[];
}
export interface DeviceFleetPage {
  items: DeviceFleetDevice[];
  total: number;
  truncated: boolean;
  summary: {
    devices: number;
    with_runner_online: number;
    with_ungoverned_agents: number;
    expected_missing: number;
    has_roster: boolean;
  };
  unassigned_agents: FleetAgent[];
}
export interface FleetFinding {
  id: string;
  rule: string;
  device_key: string | null;
  user_id: string | null;
  state: string;
  opened_at: string;
  last_evaluated_at: string;
  evidence: Record<string, unknown>;
  limitation: string;
  title: string;
  detail: string;
  href: string;
}
export interface FleetRule {
  rule: 'traffic_without_runner' | 'discovered_not_onboarded';
  enabled: boolean;
  channels: string[];
  window_minutes: number;
  grace_minutes: number;
  days: number;
}
export const fleetHref = (key?: string): string =>
  '/console/settings/device-fleet' +
  (key ? `?device=${encodeURIComponent(key)}` : '');

/** No fleet endpoint is contacted until capability and permission resolve. */
export async function fleetAccess(): Promise<{
  view: boolean;
  manage: boolean;
}> {
  const [features, user] = await Promise.all([getFeatures(), getUserProfile()]);
  const enabled = features.features.fleet_coverage === true;
  return {
    view: enabled && hasFleetPermission(user, 'view_device_fleet'),
    manage: enabled && hasFleetPermission(user, 'manage_device_fleet'),
  };
}
async function request<T>(
  path: string,
  signal?: AbortSignal,
  body?: unknown
): Promise<T> {
  const response = await fetchWithAuth(`/api/v1/fleet${path}`, {
    signal,
    ...(body === undefined
      ? {}
      : {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }),
  });
  if (!response.ok)
    throw new Error(
      response.status === 403
        ? 'You do not have permission to view device fleet.'
        : 'Device fleet could not be loaded. Retry to refresh it.'
    );
  return response.json();
}
export const getDeviceFleet = (
  signal?: AbortSignal
): Promise<DeviceFleetPage> => request('/device-fleet', signal);
export async function getFleetDevice(
  key: string,
  signal?: AbortSignal
): Promise<DeviceFleetDevice> {
  const result = await request<{
    device: DeviceFleetDevice;
    observations: Record<string, unknown>[];
  }>(`/device-fleet/detail?device_key=${encodeURIComponent(key)}`, signal);
  return { ...result.device, sources: result.observations };
}
export const getFleetFindings = (
  signal?: AbortSignal
): Promise<{ items: FleetFinding[] }> => request('/findings', signal);
interface RuleSettings {
  enabled: boolean;
  channels: Record<string, boolean>;
  window_minutes: number;
  grace_minutes: number;
  not_onboarded_days: number;
}
interface Settings {
  schema_version: 1;
  rules: Record<FleetRule['rule'], RuleSettings>;
}
function rulesFromSettings(settings: Settings): { items: FleetRule[] } {
  return {
    items: Object.entries(settings.rules).map(([rule, value]) => ({
      rule: rule as FleetRule['rule'],
      enabled: value.enabled,
      channels: Object.keys(value.channels).filter(
        (channel) => value.channels[channel]
      ),
      window_minutes: value.window_minutes,
      grace_minutes: value.grace_minutes,
      days: value.not_onboarded_days,
    })),
  };
}
export async function getFleetRules(
  signal?: AbortSignal
): Promise<{ items: FleetRule[] }> {
  return rulesFromSettings(await request<Settings>('/settings', signal));
}
export async function saveFleetRules(
  items: FleetRule[],
  signal?: AbortSignal
): Promise<{ items: FleetRule[] }> {
  const rules = Object.fromEntries(
    items.map((rule) => [
      rule.rule,
      {
        enabled: rule.enabled,
        channels: Object.fromEntries(
          ['attention', 'email', 'push', 'webhook'].map((channel) => [
            channel,
            rule.channels.includes(channel),
          ])
        ),
        window_minutes: rule.window_minutes,
        grace_minutes: rule.grace_minutes,
        not_onboarded_days: rule.days,
      },
    ])
  );
  return rulesFromSettings(
    await request<Settings>('/settings', signal, { schema_version: 1, rules })
  );
}
export function fleetFindingTitle(rule: string): string {
  return rule === 'traffic_without_runner'
    ? 'Agent traffic continued while no runner of this user was online.'
    : 'Discovered agents have not been onboarded.';
}
