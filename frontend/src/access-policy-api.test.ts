import { expect } from '@open-wc/testing';
import {
  listPolicyRules,
  writePolicyRule,
  loadSubjectOptions,
  previewResourceAccess,
  ruleDefinition,
} from './access-policy-api';
import {
  mockApi,
  signInForTest,
  type MockApi,
} from './test-helpers/capability-api';
import {
  policyRule,
  resourcePreview,
  RESOURCE_ID,
  USER_ID,
  directoryRoutes,
} from './test-helpers/access-policy';
describe('access-policy API', () => {
  let api: MockApi;
  beforeEach(() => signInForTest());
  afterEach(() => {
    api?.restore();
    localStorage.clear();
  });
  it('reads the real bare-list DTO and excludes CRUD metadata or unrelated secret fields from writes', async () => {
    const row = {
      ...policyRule(),
      created_at: '2026-01-01T00:00:00Z',
      api_key: 'must-not-be-copied',
    };
    api = mockApi({
      routes: [
        { path: '/api/v1/access/rules', body: [row] },
        {
          method: 'PUT',
          path: `/api/v1/access/rules/${row.id}`,
          body: policyRule(),
        },
      ],
    });
    const [loaded] = await listPolicyRules();
    await writePolicyRule(loaded, 2);
    const body = api.callsTo(`/api/v1/access/rules/${row.id}`, 'PUT')[0].body;
    expect(body).to.eql({
      ...ruleDefinition(policyRule()),
      expected_version: 2,
    });
    expect(JSON.stringify(body)).not.to.contain('api_key');
    expect(JSON.stringify(body)).not.to.contain('created_at');
  });
  it('paginates tenant directories and uses names only for display', async () => {
    api = mockApi({
      routes: [
        {
          path: '/api/v1/users',
          body: (call) => ({
            users: call.search.includes('skip=0&')
              ? [{ id: USER_ID, full_name: 'Jane Doe' }]
              : [{ id: RESOURCE_ID, username: 'reviewer' }],
            total: 2,
          }),
        },
        ...directoryRoutes.slice(1),
      ],
    });
    const options = await loadSubjectOptions(['view_users', 'view_teams']);
    expect(options.filter((option) => option.kind === 'user')).to.have.length(
      2
    );
    expect(api.callsTo('/api/v1/users').map((call) => call.search)).to.eql([
      '?skip=0&limit=100',
      '?skip=1&limit=100',
    ]);
  });
  it('caps each paginated directory at five requests and 500 names', async () => {
    api = mockApi({
      routes: [
        {
          path: '/api/v1/users',
          body: {
            users: Array.from({ length: 100 }, (_, i) => ({
              id: `user-${i}`,
              username: `User ${i}`,
            })),
            total: 100000,
          },
        },
        {
          path: '/api/v1/teams',
          body: {
            teams: Array.from({ length: 100 }, (_, i) => ({
              id: `team-${i}`,
              name: `Team ${i}`,
            })),
            total: 100000,
          },
        },
        ...directoryRoutes.slice(2),
      ],
    });
    const options = await loadSubjectOptions(['view_users', 'view_teams']);
    expect(api.callsTo('/api/v1/users')).to.have.length(5);
    expect(api.callsTo('/api/v1/teams')).to.have.length(5);
    expect(options.filter((option) => option.kind === 'user')).to.have.length(
      500
    );
    expect(options.filter((option) => option.kind === 'team')).to.have.length(
      500
    );
  });
  it('does not probe directories without permission', async () => {
    api = mockApi();
    expect(await loadSubjectOptions(['view_policies'])).to.eql([]);
    expect(api.calls).to.have.length(0);
  });
  it('keeps preview payload limited to the supplied access document and expected version', async () => {
    api = mockApi({
      routes: [
        {
          method: 'POST',
          path: `/api/v1/access/resources/ai_model/${RESOURCE_ID}/preview`,
          body: resourcePreview(),
        },
      ],
    });
    const document = {
      rules: [ruleDefinition(policyRule())],
      requirements: [],
    };
    await previewResourceAccess('ai_model', RESOURCE_ID, document, 3);
    expect(api.calls[0].body).to.eql({ document, expected_version: 3 });
    expect(JSON.stringify(api.calls[0].body)).not.to.contain('version":2');
  });
});
