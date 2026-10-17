import { expect } from '@open-wc/testing';
import {
  mayPrompt,
  supportChoices,
  type EngagementState,
} from './support-engagement';
describe('Optional support choices', () => {
  const ready: EngagementState = {
    activeMs: 30000,
    idleMs: 12000,
    hidden: false,
    focusedForm: false,
    dismissed: false,
    converted: false,
    prompted: false,
  };
  it('requires both active dwell and idle thresholds', () => {
    expect(mayPrompt(ready)).to.equal(true);
    expect(mayPrompt({ ...ready, activeMs: 29999 })).to.equal(false);
    expect(mayPrompt({ ...ready, idleMs: 11999 })).to.equal(false);
    expect(mayPrompt(ready, 60000, 12000)).to.equal(false);
  });
  for (const flag of [
    'hidden',
    'focusedForm',
    'dismissed',
    'converted',
    'prompted',
  ] as const)
    it(`suppresses when ${flag}`, () =>
      expect(mayPrompt({ ...ready, [flag]: true })).to.equal(false));
  it('offers page-specific choices without an email gate', () => {
    expect(supportChoices('pricing')).to.include('Compare plans');
    expect(supportChoices('signup')).to.include('Security questions');
    expect(supportChoices('home')).to.include('Explore a use case');
  });
});
