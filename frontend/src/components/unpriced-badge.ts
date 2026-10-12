import { html, nothing } from 'lit';
import '@shoelace-style/shoelace/dist/components/badge/badge.js';

/**
 * "Unpriced" for a deployment-provided hosted model with no verified operator
 * tariff. The API only sends such rows (``unpriced: true``) to platform
 * admins, so an operator can find and fix the gap; renders nothing otherwise.
 */
export function unpricedBadge(
  model: { unpriced?: boolean } | null | undefined
) {
  if (!model?.unpriced) return nothing;
  return html`<sl-badge
    class="unpriced"
    variant="warning"
    pill
    data-testid="unpriced-badge"
    title="No verified operator tariff: hidden from non-admins, requests are refused"
    >unpriced</sl-badge
  >`;
}
