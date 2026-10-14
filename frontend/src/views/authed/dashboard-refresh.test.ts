import { expect } from '@open-wc/testing';
import sinon from 'sinon';
import './dashboard-control-plane-view';
import {
  REALTIME_TOPIC_INTERVAL_MS,
  type DashboardView,
} from './dashboard-control-plane-view';

describe('Overview refresh backpressure', () => {
  let clock: sinon.SinonFakeTimers;
  let element: DashboardView;

  beforeEach(() => {
    clock = sinon.useFakeTimers({ now: Date.now() });
    element = document.createElement('dashboard-view') as DashboardView;
    sinon.stub(element, 'isConnected').get(() => true);
  });
  afterEach(() => {
    clock.restore();
    sinon.restore();
  });

  it('delivers queued events even when initial loading finishes within five seconds', async () => {
    element['refreshInFlight'] = true;
    element['lastFetchStartedAt'] = Date.now();
    const run = sinon.stub().resolves();
    element['scheduleTopicRefresh']('fleet', run);
    await clock.tickAsync(1000);
    element['refreshInFlight'] = false;
    // A load this short has not outlived the topic floor, so arming it
    // must not push the queued refresh out to a fresh interval.
    element['armTopicFloorAfterLongLoad']();
    element['flushPendingTopicRefreshes']();
    await clock.tickAsync(3999);
    expect(run.called).to.equal(false);
    await clock.tickAsync(1);
    expect(run.calledOnce).to.equal(true);
  });

  it('coalesces trailing refreshes after a load that outlasted the topic floor', async () => {
    // The cold-load case: audit overview holds the page open for longer
    // than the floor, websocket events queue one refresh per topic, and
    // the load then ends. Those refreshes must not start on the debounce.
    // They do not overlap the load's own GETs, so in-flight dedupe cannot
    // collapse them.
    element['refreshInFlight'] = true;
    element['lastFetchStartedAt'] = Date.now() - 23_000;
    const gateway = sinon.stub().resolves();
    const fleet = sinon.stub().resolves();
    const budget = sinon.stub().resolves();
    element['scheduleTopicRefresh']('gateway', gateway);
    element['scheduleTopicRefresh']('gateway', gateway);
    element['scheduleTopicRefresh']('fleet', fleet);
    element['scheduleTopicRefresh']('budget', budget);
    element['armTopicFloorAfterLongLoad']();
    element['refreshInFlight'] = false;
    element['flushPendingTopicRefreshes']();

    await clock.tickAsync(REALTIME_TOPIC_INTERVAL_MS - 1);
    expect(gateway.called, 'gateway refresh stayed behind the floor').to.equal(
      false
    );
    expect(fleet.called).to.equal(false);
    expect(budget.called).to.equal(false);

    await clock.tickAsync(1);
    expect(gateway.calledOnce, 'one coalesced gateway refresh').to.equal(true);
    expect(fleet.calledOnce).to.equal(true);
    expect(budget.calledOnce).to.equal(true);
  });

  it('does not launch realtime requests during a slow initial load', async () => {
    element['refreshInFlight'] = true;
    element['lastFetchStartedAt'] = Date.now();
    const run = sinon.stub().resolves();
    await clock.tickAsync(27000);
    element['scheduleTopicRefresh']('fleet', run);
    await clock.tickAsync(10000);
    expect(run.called).to.equal(false);
    element['refreshInFlight'] = false;
    element['scheduleTopicRefresh']('fleet', run);
    await clock.tickAsync(10000);
    expect(run.calledOnce).to.equal(true);
  });

  it('keeps one request in flight after the realtime interval has elapsed', async () => {
    let release!: () => void;
    const run = sinon.stub().callsFake(
      () =>
        new Promise<void>((resolve) => {
          release = resolve;
        })
    );
    element['scheduleTopicRefresh']('fleet', run);
    await clock.tickAsync(10000);
    element['scheduleTopicRefresh']('fleet', run);
    await clock.tickAsync(27000);
    expect(run.calledOnce).to.equal(true);
    release();
    await clock.tickAsync(0);
    await clock.tickAsync(10000);
    expect(run.calledTwice).to.equal(true);
    release();
    await clock.tickAsync(0);
  });

  it('rechecks initial loading when an already scheduled event fires', async () => {
    const run = sinon.stub().resolves();
    element['scheduleTopicRefresh']('fleet', run);
    element['refreshInFlight'] = true;
    await clock.tickAsync(10000);
    expect(run.called).to.equal(false);
  });

  it('coalesces background refreshes and releases the guard on failure', async () => {
    let release!: () => void;
    const breakdown = sinon
      .stub(element as any, 'refreshUsageBreakdown')
      .callsFake(
        () =>
          new Promise<void>((resolve) => {
            release = resolve;
          })
      );
    sinon.stub(element as any, 'refreshAttentionInputs').resolves();
    sinon.stub(element as any, 'refreshAuditExceptions').resolves();
    const first = element['refreshBackgroundInputs']();
    await element['refreshBackgroundInputs']();
    expect(breakdown.calledOnce).to.equal(true);
    release();
    await first;
    breakdown.rejects(new Error('temporary failure'));
    try {
      await element['refreshBackgroundInputs']();
    } catch {
      /* expected */
    }
    expect(element['backgroundRefreshInFlight']).to.equal(false);
  });

  it('does not let a queued soft reload replace a full reload', async () => {
    element['refreshInFlight'] = true;
    await element['fetchDashboardData']();
    await element['fetchDashboardData']({ preserveLoadingState: true });
    expect(element['queuedDashboardRefresh']).to.deep.equal({});
  });

  it('promotes a queued soft reload when a full reload arrives', async () => {
    element['refreshInFlight'] = true;
    await element['fetchDashboardData']({ preserveLoadingState: true });
    await element['fetchDashboardData']();
    expect(element['queuedDashboardRefresh']).to.deep.equal({});
  });

  it('keeps a queued range change soft when only soft reloads arrive', async () => {
    element['refreshInFlight'] = true;
    await element['fetchDashboardData']({ preserveLoadingState: true });
    await element['fetchDashboardData']({ preserveLoadingState: true });
    expect(element['queuedDashboardRefresh']).to.deep.equal({
      preserveLoadingState: true,
    });
  });
});
