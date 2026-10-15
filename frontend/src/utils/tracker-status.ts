import { html, nothing } from 'lit';

export interface TrackerStatus {
  auth_type?: string;
  authorization_state?: string | null;
  is_valid: boolean;
  last_validation?: string | null;
  validation_message?: string | null;
}

export function validationStatus(tracker: TrackerStatus) {
  if (tracker.is_valid)
    return { text: 'Last check passed', variant: 'success' };
  if (tracker.last_validation || tracker.validation_message)
    return { text: 'Last check failed', variant: 'danger' };
  return {
    text:
      tracker.auth_type?.toLowerCase() === 'managed_oauth'
        ? 'Not synced yet'
        : 'Not yet checked',
    variant: 'neutral',
  };
}

export function authorizationStatus(state?: string | null) {
  switch (state) {
    case 'connected':
      return { text: 'Authorized', variant: 'success' };
    case 'site_required':
      return { text: 'Choose site', variant: 'warning' };
    case 'workspace_required':
      return { text: 'Choose workspace', variant: 'warning' };
    case 'reconnect_required':
      return { text: 'Reconnect required', variant: 'danger' };
    case 'disconnected':
      return { text: 'Disconnected', variant: 'danger' };
    case 'unavailable':
      return { text: 'Provider unavailable', variant: 'warning' };
    default:
      return { text: 'Authorization unknown', variant: 'neutral' };
  }
}

/** Shared by table, cards and detail: grant authorization never implies sync. */
export function renderTrackerStatus(tracker: TrackerStatus) {
  const validation = validationStatus(tracker);
  const authorization = authorizationStatus(tracker.authorization_state);
  return html`
    ${
      tracker.auth_type?.toLowerCase() === 'managed_oauth'
        ? html`<sl-badge
            class="chip managed-connection"
            data-state=${tracker.authorization_state ?? 'unknown'}
            variant=${authorization.variant as any}
            pill
            >${authorization.text}</sl-badge
          >`
        : nothing
    }
    <sl-badge
      class="chip tracker-validation"
      variant=${validation.variant as any}
      pill
      title=${tracker.last_validation ? `Last check: ${tracker.last_validation}` : ''}
      >${validation.text}</sl-badge
    >
  `;
}
