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
  epoch += 1;
}

onApiCachesInvalidated(resetKillSwitchStore);
