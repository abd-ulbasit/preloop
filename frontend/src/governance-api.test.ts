import { expect } from '@open-wc/testing';
import sinon from 'sinon';
import {
  dismissGovernanceFinding,
  getGovernanceFindings,
  restoreGovernanceFinding,
} from './governance-api';

describe('governance action guidance', () => {
  afterEach(() => {
    sinon.restore();
    localStorage.clear();
  });
  for (const status of [402, 429]) {
    it(`shows ${status} guidance for dismissal and restore, while reads stay passive`, async () => {
      localStorage.setItem('accessToken', 'test-access-token');
      sinon.stub(window, 'fetch').callsFake(
        async () =>
          new Response(
            JSON.stringify({
              detail:
                status === 402
                  ? { code: 'upgrade_required', feature: 'governance_review' }
                  : 'Try again shortly.',
            }),
            { status, headers: { 'Content-Type': 'application/json' } }
          )
      );
      const events: Event[] = [];
      const eventName = status === 402 ? 'show-upgrade-modal' : 'show-toast';
      const listener = (event: Event) => events.push(event);
      window.addEventListener(eventName, listener);
      try {
        await getGovernanceFindings({}).catch(() => undefined);
        expect(events).to.have.length(0);
        await dismissGovernanceFinding('finding-1', 'fixed').catch(
          () => undefined
        );
        expect(events).to.have.length(1);
        await restoreGovernanceFinding('finding-1').catch(() => undefined);
        expect(events).to.have.length(2);
      } finally {
        window.removeEventListener(eventName, listener);
      }
    });
  }
});
