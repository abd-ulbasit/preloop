import { expect } from '@open-wc/testing';
import sinon from 'sinon';
import {
  getKillSwitchSnapshot,
  holdKillSwitchPoll,
  KILL_SWITCH_POLL_MS,
  refreshKillSwitch,
  resetKillSwitchStore,
  setKillSwitchStatus,
  subscribeKillSwitch,
} from './kill-switch-store';
import type { KillSwitchStatus } from '../types';

const HALTED: KillSwitchStatus = {
  active: true,
  scopes: [
    {
      scope: 'gateway',
      activated_by_user_id: 'u',
      activated_by_username: 'jdoe',
      activated_at: '2026-10-12T10:00:00Z',
      reason: null,
    },
  ],
};
const CLEAR: KillSwitchStatus = { active: false, scopes: [] };

describe('kill-switch-store', () => {
  let fetchStub: sinon.SinonStub;
  let answer: KillSwitchStatus | null;

  beforeEach(() => {
    resetKillSwitchStore();
    localStorage.setItem('accessToken', 'test-token');
    answer = CLEAR;
    fetchStub = sinon.stub(window, 'fetch').callsFake(async () =>
      answer
        ? new Response(JSON.stringify(answer), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          })
        : new Response('{}', { status: 500 })
    );
  });

  afterEach(() => {
    fetchStub.restore();
    localStorage.removeItem('accessToken');
  });

  function statusCalls(): number {
    return fetchStub
      .getCalls()
      .filter((call) => String(call.args[0]).includes('kill-switch/status'))
      .length;
  }

  it('answers two readers on one page with one request', async () => {
    const seen: Array<KillSwitchStatus | null> = [];
    const offBanner = subscribeKillSwitch((status) => seen.push(status));
    const offSettings = subscribeKillSwitch((status) => seen.push(status));
    await refreshKillSwitch();
    expect(statusCalls()).to.equal(1);
    expect(seen.filter((status) => status?.active === false).length).to.equal(
      2
    );
    offBanner();
    offSettings();
  });

  it('gives a late reader the known state without asking again', async () => {
    const off = subscribeKillSwitch(() => undefined);
    await refreshKillSwitch();
    const late: Array<KillSwitchStatus | null> = [];
    const offLate = subscribeKillSwitch((status) => late.push(status));
    await refreshKillSwitch();
    expect(late[0]).to.deep.equal(CLEAR);
    expect(statusCalls()).to.equal(1);
    off();
    offLate();
  });

  it('tells every reader about a halt written from this tab', () => {
    const seen: Array<KillSwitchStatus | null> = [];
    const off = subscribeKillSwitch((status) => seen.push(status));
    setKillSwitchStatus(HALTED);
    expect(seen[seen.length - 1]).to.equal(HALTED);
    expect(getKillSwitchSnapshot()).to.equal(HALTED);
    off();
  });

  it('keeps a state this tab wrote over a read that started before it', async () => {
    answer = CLEAR;
    const pending = refreshKillSwitch({ force: true });
    setKillSwitchStatus(HALTED);
    await pending;
    expect(getKillSwitchSnapshot()).to.equal(HALTED);
  });

  it('keeps the last known state when a read fails', async () => {
    setKillSwitchStatus(HALTED);
    answer = null;
    const failures: boolean[] = [];
    const off = subscribeKillSwitch((_status, failed) => failures.push(failed));
    await refreshKillSwitch({ force: true });
    expect(getKillSwitchSnapshot()).to.equal(HALTED);
    expect(failures[failures.length - 1]).to.equal(true);
    off();
  });

  it('skips the banner poll while a cold load is still reading', async () => {
    const clock = sinon.useFakeTimers();
    const release = holdKillSwitchPoll();
    const off = subscribeKillSwitch(() => undefined);
    try {
      await clock.tickAsync(0);
      expect(statusCalls(), 'the initial read still happens').to.equal(1);

      await clock.tickAsync(KILL_SWITCH_POLL_MS);
      expect(
        statusCalls(),
        'the poll waits until the load releases it'
      ).to.equal(1);

      release();
      await clock.tickAsync(KILL_SWITCH_POLL_MS);
      expect(statusCalls(), 'the following interval asks again').to.equal(2);
    } finally {
      off();
      release();
      clock.restore();
    }
  });

  it('drops a read that started before a reset', async () => {
    const pending = refreshKillSwitch();
    resetKillSwitchStore();
    await pending;
    expect(getKillSwitchSnapshot()).to.equal(null);
  });
});
