/** Generic fixtures matching merged EE ResourceAccessSummary/Preview DTOs. */
import {
  emptyRule,
  type ResourceSummary,
  type ResourcePreview,
  type StoredRule,
} from '../access-policy-api';
export const RESOURCE_ID = '11111111-1111-4111-8111-111111111111';
export const RULE_ID = '22222222-2222-4222-8222-222222222222';
export const USER_ID = '33333333-3333-4333-8333-333333333333';
export const TEAM_ID = '44444444-4444-4444-8444-444444444444';
export const ROLE_ID = '55555555-5555-4555-8555-555555555555';
export const ACCOUNT_ID = '66666666-6666-4666-8666-666666666666';
export const SUBJECTS = [
  { kind: 'user' as const, id: USER_ID, label: 'Jane Doe' },
  { kind: 'team' as const, id: TEAM_ID, label: 'Review team' },
  { kind: 'role' as const, id: ROLE_ID, label: 'Operator' },
];
export const policyRule = (): StoredRule => ({
  ...emptyRule(),
  id: RULE_ID,
  name: 'Review team permit',
  actions: ['model:invoke'],
  subject_selector: { teams: [TEAM_ID] },
  resource_type: 'ai_model',
  resource_selector: { ids: [RESOURCE_ID] },
  version: 2,
  account_id: ACCOUNT_ID,
  inherited: false,
  editable: true,
});
export const resourceSummary = (): ResourceSummary => ({
  document: {
    account_id: ACCOUNT_ID,
    resource_type: 'ai_model',
    resource_id: RESOURCE_ID,
    version: 3,
    generation: 8,
    rules: [],
    requirements: [],
  },
  supported_actions: ['resource:view', 'model:invoke'],
  can_manage: true,
  inherited_policies: [],
  applied_rules: [],
  sharing: { supported: true, can_manage: false, intents: [] },
});
export const resourcePreview = (): ResourcePreview => ({
  version: 3,
  generation: 8,
  changes: [
    {
      kind: 'user',
      id: USER_ID,
      flow_id: null,
      action: 'model:invoke',
      before: 'allow',
      after: 'deny',
    },
  ],
  scope: 'policy_only',
  runtime_context: 'unknown',
  expires_at: '2099-01-01T00:00:00Z',
  preview_token: 'generic-preview-token',
});
export const directoryRoutes = [
  {
    path: '/api/v1/users',
    body: {
      users: [{ id: USER_ID, username: 'jane', full_name: 'Jane Doe' }],
      total: 1,
      skip: 0,
      limit: 100,
    },
  },
  {
    path: '/api/v1/teams',
    body: {
      teams: [{ id: TEAM_ID, name: 'Review team' }],
      total: 1,
      skip: 0,
      limit: 100,
    },
  },
  {
    path: '/api/v1/roles',
    body: { roles: [{ id: ROLE_ID, name: 'Operator' }], total: 1 },
  },
];
