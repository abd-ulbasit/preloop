import { expect } from '@open-wc/testing';
import sinon from 'sinon';
import {
  getUserProfile,
  invalidateApiCaches,
  updateUserProfile,
  uploadAvatar,
  deleteAvatar,
} from './api';

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

for (const operation of ['upload', 'delete'] as const) {
  it(`invalidates an earlier profile read after avatar ${operation}`, async () => {
    invalidateApiCaches();
    localStorage.setItem('accessToken', 'test-token');
    let release!: (response: Response) => void;
    const earlier = new Promise<Response>((resolve) => (release = resolve));
    let reads = 0;
    const avatar = operation === 'upload' ? 'new-avatar' : null;
    const json = (avatar_url: string | null) =>
      new Response(
        JSON.stringify({
          id: 'membership-example',
          person_id: 'person-example',
          avatar_url,
        })
      );
    const stub = sinon.stub(window, 'fetch').callsFake(async (_url, init) => {
      if (init?.method === 'PUT' || init?.method === 'DELETE')
        return json(avatar);
      return ++reads === 1 ? earlier : json(avatar);
    });
    try {
      const stale = getUserProfile();
      if (operation === 'upload')
        await uploadAvatar(new File(['example'], 'avatar.png'));
      else await deleteAvatar();
      release(json('old-avatar'));
      await stale;
      expect((await getUserProfile()).avatar_url).to.equal(avatar);
      expect(reads).to.equal(2);
    } finally {
      stub.restore();
      localStorage.clear();
      invalidateApiCaches();
    }
  });
}
