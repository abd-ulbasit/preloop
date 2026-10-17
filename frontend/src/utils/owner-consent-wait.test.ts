import { expect } from '@open-wc/testing';
import {
  WAITING_FOR_RUNNER_OWNER,
  isWaitingForRunnerOwner,
} from './execution-presentation';

describe('isWaitingForRunnerOwner', () => {
  for (const status of [
    'PENDING',
    'QUEUED',
    'STARTING',
    'RUNNING',
    'running',
  ]) {
    it(`reads ${status} runs held for the owner as a wait`, () => {
      expect(
        isWaitingForRunnerOwner({
          status,
          error_message: WAITING_FOR_RUNNER_OWNER,
        })
      ).to.equal(true);
    });
  }

  it('still reads it as a wait once the orchestrator marks the row running', () => {
    expect(
      isWaitingForRunnerOwner({
        status: 'RUNNING',
        error_message: WAITING_FOR_RUNNER_OWNER,
      })
    ).to.equal(true);
  });

  it('leaves finished runs and other messages alone', () => {
    for (const status of [
      'FAILED',
      'COMPLETED',
      'SUCCEEDED',
      'STOPPED',
      'TIMEOUT',
      'CANCELLED',
    ]) {
      expect(
        isWaitingForRunnerOwner({
          status,
          error_message: WAITING_FOR_RUNNER_OWNER,
        })
      ).to.equal(false);
    }
    expect(
      isWaitingForRunnerOwner({ status: 'PENDING', error_message: 'Other' })
    ).to.equal(false);
  });
});
