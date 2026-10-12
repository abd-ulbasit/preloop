import { expect } from '@open-wc/testing';
import sinon from 'sinon';
import { getUserProfile, invalidateApiCaches, updateUserProfile } from './api';

describe('canonical profile cache after an edit', () => {
  it('does not let an earlier in-flight profile read replace the updated identity', async () => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-token');
    let release!: (response: Response) => void;
    const earlier = new Promise<Response>((resolve) => {
      release = resolve;
    });
    let reads = 0;
    const json = (username: string) =>
      new Response(
        JSON.stringify({
          id: 'membership-example',
          person_id: 'person-example',
          username,
          email: 'jane@example.com',
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }
      );
    const stub = sinon.stub(window, 'fetch').callsFake(async (_url, init) => {
      if (init?.method === 'PUT') return json('updated-person');
      return ++reads === 1 ? earlier : json('updated-person');
    });
    try {
      const stale = getUserProfile();
      await updateUserProfile({ username: 'updated-person' });
      const current = await getUserProfile();
      expect(current.username).to.equal('updated-person');
      release(json('old-person'));
      await stale;
      expect((await getUserProfile()).username).to.equal('updated-person');
      expect(reads).to.equal(2);
    } finally {
      stub.restore();
      localStorage.clear();
      invalidateApiCaches();
    }
  });
});
