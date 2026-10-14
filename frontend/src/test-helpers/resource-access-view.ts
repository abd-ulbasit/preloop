/** Persisted generic resource state for header integration and visual checks. */
import type { LitElement } from 'lit';
import '../views/authed/agent-detail-view';
import '../views/authed/settings/ai-model-detail-view';
import '../views/authed/flow-view';

export type ResourceAccessViewKind = 'managed_agent' | 'ai_model' | 'flow';
export function resourceAccessView(kind: ResourceAccessViewKind): LitElement {
  const common = { initialized: true, loading: false };
  if (kind === 'managed_agent') {
    return Object.assign(
      document.createElement('agent-detail-view') as LitElement,
      common,
      {
        agentId: 'agent-1',
        agent: {
          id: 'agent-1',
          display_name: 'Review agent',
          session_source_type: 'codex',
          session_source_id: 'review-agent',
          lifecycle_state: 'active',
          activity_status: 'idle',
          managed_mcp_servers: [],
          enrolled_via: 'api_key',
          last_seen_at: '2026-10-01T12:00:00Z',
          owner_username: 'owner',
          tags: {},
        },
        aggregate: {
          total_requests: 24,
          successful_requests: 24,
          failed_requests: 0,
          estimated_cost: 0.12,
          token_usage: {
            prompt_tokens: 1200,
            completion_tokens: 400,
            total_tokens: 1600,
          },
        },
      }
    );
  }
  if (kind === 'ai_model') {
    return Object.assign(
      document.createElement('ai-model-detail-view') as LitElement,
      common,
      {
        modelId: 'model-1',
        model: {
          id: 'model-1',
          name: 'Review model',
          model_identifier: 'review-model',
          provider_name: 'Example provider',
          updated_at: '2026-10-01T12:00:00Z',
          meta_data: {},
        },
        summaryLoading: false,
        sessionsLoading: false,
        summary: {
          total_requests: 24,
          successful_requests: 24,
          failed_requests: 0,
          estimated_cost: 0.12,
          token_usage: {
            prompt_tokens: 1200,
            completion_tokens: 400,
            total_tokens: 1600,
          },
          requests_by_day: [],
          period_start: '2026-10-01T00:00:00Z',
          period_end: '2026-10-14T00:00:00Z',
        },
      }
    );
  }
  return Object.assign(
    document.createElement('flow-view') as LitElement,
    common,
    {
      flowId: 'flow-1',
      flowReady: true,
      isNew: false,
      isEditing: false,
      flow: {
        id: 'flow-1',
        name: 'Review flow',
        description: 'Review incoming pull requests.',
        agent_type: 'codex',
        is_enabled: true,
        trigger_event_source: 'webhook',
        prompt_template:
          'Review the proposed change and report actionable findings.',
      },
    }
  );
}
