import { expect } from '@open-wc/testing';
import { unifiedWebSocketManager } from './unified-websocket-manager';

describe('unifiedWebSocketManager control messages', () => {
  function deliver(message: unknown): void {
    (
      unifiedWebSocketManager as unknown as {
        handleMessage(event: MessageEvent): void;
      }
    ).handleMessage(
      new MessageEvent('message', { data: JSON.stringify(message) })
    );
  }

  it('does not hand a refused subscription to that topic as news', () => {
    const received: unknown[] = [];
    const unsubscribe = unifiedWebSocketManager.subscribe(
      'device_registered',
      (message) => received.push(message)
    );
    try {
      deliver({
        type: 'subscription_error',
        topic: 'device_registered',
        error: 'Unsupported topic',
      });
      deliver({
        type: 'subscription_ack',
        topic: 'device_registered',
        action: 'subscribed',
      });
      expect(received).to.deep.equal([]);

      deliver({ type: 'device_registered', platform: 'ios' });
      expect(received).to.have.length(1);
    } finally {
      unsubscribe();
    }
  });
});
