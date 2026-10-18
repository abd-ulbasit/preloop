import { expect } from '@open-wc/testing';
import sinon from 'sinon';

import {
  MAX_CONCURRENT_ARTIFACT_LOADS,
  acquireSessionArtifact,
  activeSessionArtifactLoads,
  releaseSessionArtifact,
} from './session-artifacts';

describe('session artifact loads (#1690)', () => {
  let fetchStub: sinon.SinonStub;
  let pending: Array<() => void>;

  beforeEach(() => {
    localStorage.setItem('accessToken', 'test-access-token');
    pending = [];
    fetchStub = sinon.stub(window, 'fetch').callsFake(
      () =>
        new Promise<Response>((resolve) => {
          pending.push(() =>
            resolve(new Response(new Blob(['x'], { type: 'image/png' })))
          );
        })
    );
  });

  afterEach(() => {
    fetchStub.restore();
    localStorage.clear();
  });

  const flush = () => new Promise((resolve) => setTimeout(resolve, 0));

  it('keeps at most two artifact downloads in flight', async () => {
    const ids = ['a1', 'a2', 'a3', 'a4', 'a5'];
    const loads = ids.map((id) => acquireSessionArtifact('s-limit', id));
    await flush();

    expect(fetchStub.callCount).to.equal(MAX_CONCURRENT_ARTIFACT_LOADS);
    expect(activeSessionArtifactLoads()).to.equal(2);

    while (pending.length) {
      pending.shift()!();
      await flush();
      await flush();
    }
    const results = await Promise.all(loads);
    expect(results.every((r) => r.status === 'ok')).to.equal(true);
    expect(fetchStub.callCount).to.equal(ids.length);
    expect(activeSessionArtifactLoads()).to.equal(0);
    ids.forEach((id) => releaseSessionArtifact('s-limit', id));
  });

  it('skips a queued download released before it started', async () => {
    const first = acquireSessionArtifact('s-cancel', 'b1');
    const second = acquireSessionArtifact('s-cancel', 'b2');
    const third = acquireSessionArtifact('s-cancel', 'b3');
    await flush();
    releaseSessionArtifact('s-cancel', 'b3');

    while (pending.length) {
      pending.shift()!();
      await flush();
      await flush();
    }
    await Promise.all([first, second]);
    const skipped = await third;

    expect(skipped.status).to.equal('error');
    expect(fetchStub.callCount).to.equal(2);
    releaseSessionArtifact('s-cancel', 'b1');
    releaseSessionArtifact('s-cancel', 'b2');
  });
});
