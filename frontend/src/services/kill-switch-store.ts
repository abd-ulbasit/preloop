import { getKillSwitchStatus, onApiCachesInvalidated } from '../api';
import type { KillSwitchStatus } from '../types';

/**
 * The account's halt state, read once and shared.
 *
 * The console banner polls it on every page and the Emergency settings page
 * shows it too. Each used to ask on its own, so one page made two reads and
 * a slow page made three as the banner's poll came round. Now there is one
 * poll for the whole console, one request in the air at a time, and every
 * reader is told when the answer changes, including after a halt or resume
 * made from this tab.
 */

/** How often the shared poll asks while the tab is visible. */
export const KILL_SWITCH_POLL_MS = 15000;

/** A read younger than this is answered from memory. */
const FRESH_MS = 5000;

export type KillSwitchListener = (
  status: KillSwitchStatus | null,
  failed: boolean
) => void;

let status: KillSwitchStatus | null = null;
let readAt = 0;
let lastFailed = false;
let inflight: Promise<KillSwitchStatus | null> | null = null;
let pollTimer: number | undefined;
/**
 * Pages that are still on their first read. The interval skips a turn
 * while this is above zero so a slow Overview does not ask for
 * kill-switch status again in the middle of that load. The initial read
 * still happens; the next poll is the following interval.
 */
let pollHoldCount = 0;
/**
 * Polls skipped while a hold is up. One skip covers the cold Overview.
 * The tick after that asks anyway, so a load that never finishes cannot
 * silence the halt banner.
 */
let heldSkips = 0;
/** Bumped by a reset so a read from before it cannot land after it. */
let epoch = 0;
const listeners = new Set<KillSwitchListener>();

function notify(): void {
  for (const listener of listeners) listener(status, lastFailed);
}

/**
 * Read the halt state. Joins a read already in the air, and answers from
 * memory when the last read is only seconds old unless `force` is set.
 * Never rejects: a failed read keeps the last known state and reports
 * `failed` to the listeners.
 */
export function refreshKillSwitch(
  options: { force?: boolean } = {}
): Promise<KillSwitchStatus | null> {
  if (inflight) return inflight;
  if (!options.force && status && Date.now() - readAt < FRESH_MS) {
    return Promise.resolve(status);
  }
  const started = epoch;
  const request: Promise<KillSwitchStatus | null> = getKillSwitchStatus()
    .then((next) => {
      if (started !== epoch) return status;
      status = next;
      readAt = Date.now();
      lastFailed = false;
      return next;
    })
    .catch(() => {
      if (started === epoch) lastFailed = true;
      return status;
    })
    .finally(() => {
      if (inflight === request) inflight = null;
      if (started === epoch) notify();
    });
  inflight = request;
  return request;
}

/** Record a state this tab just wrote (halt or resume) and tell everyone. */
export function setKillSwitchStatus(next: KillSwitchStatus): void {
  // A read that started before this write must not land after it and put
  // the old state back.
  inflight = null;
  epoch += 1;
  status = next;
  readAt = Date.now();
  lastFailed = false;
  notify();
}

/** The last state read, or null before the first answer. */
export function getKillSwitchSnapshot(): KillSwitchStatus | null {
  return status;
}

/**
 * Hold the shared poll until `release`.
 *
 * The banner's interval is 15s. A cold Overview whose audit read takes
 * longer than that used to fire the poll inside the load, which is a
 * second `GET /account/kill-switch/status` for data the page already has.
 * The pairs do not overlap, so in-flight dedupe does not collapse them.
 * A hold skips one tick only. The tick after that asks even if the load
 * is still running, so a hung read cannot hide a halt.
 */
export function holdKillSwitchPoll(): () => void {
  pollHoldCount += 1;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    pollHoldCount = Math.max(0, pollHoldCount - 1);
    if (pollHoldCount === 0) heldSkips = 0;
  };
}

/**
 * Listen for the halt state. The first listener starts the shared poll and
 * the last one to leave stops it. A hidden tab skips its turn.
 */
export function subscribeKillSwitch(listener: KillSwitchListener): () => void {
  listeners.add(listener);
  if (status) listener(status, lastFailed);
  if (listeners.size === 1) {
    pollTimer = window.setInterval(() => {
      if (
        typeof document !== 'undefined' &&
        document.visibilityState === 'hidden'
      )
        return;
      // One skipped tick is the cold load. A second tick while the hold
      // is still up means the load did not finish; ask anyway.
      if (pollHoldCount > 0 && heldSkips < 1) {
        heldSkips += 1;
        return;
      }
      heldSkips = 0;
      void refreshKillSwitch({ force: true });
    }, KILL_SWITCH_POLL_MS);
  }
  void refreshKillSwitch();
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && pollTimer !== undefined) {
      window.clearInterval(pollTimer);
      pollTimer = undefined;
    }
  };
}

/** Forget everything: sign-out, and between tests. */
export function resetKillSwitchStore(): void {
  status = null;
  readAt = 0;
  lastFailed = false;
  inflight = null;
  pollHoldCount = 0;
  heldSkips = 0;
  epoch += 1;
}

onApiCachesInvalidated(resetKillSwitchStore);
