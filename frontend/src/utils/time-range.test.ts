import { expect } from '@open-wc/testing';

import {
  formatTimeRangeWindow,
  normalizeTimeRangeKey,
  resolvePreviousTimeRange,
  resolveTimeRange,
  timeRangeComparisonLabel,
  timeRangePhrase,
  timeRangeShortLabel,
} from './time-range';

describe('time-range', () => {
  // A fixed local instant, so the assertions below are about the math and not
  // about the day the suite runs.
  const now = new Date(2026, 8, 6, 14, 30, 0); // 6 Sep 2026, 14:30 local

  it('reads every spelling the console grew for the same window', () => {
    expect(normalizeTimeRangeKey('month')).to.equal('last-30');
    expect(normalizeTimeRangeKey('30d')).to.equal('last-30');
    expect(normalizeTimeRangeKey('last-30')).to.equal('last-30');
    expect(normalizeTimeRangeKey('day')).to.equal('last-24h');
    expect(normalizeTimeRangeKey('1y')).to.equal('last-365');
    expect(normalizeTimeRangeKey('nonsense')).to.equal(null);
    expect(normalizeTimeRangeKey('')).to.equal(null);
  });

  it('defaults a missing or empty key to last-30', () => {
    const canonical = resolveTimeRange('last-30', now);
    const previous = resolvePreviousTimeRange('last-30', now);
    expect(resolveTimeRange('', now)).to.deep.equal(canonical);
    expect(resolveTimeRange('   ', now)).to.deep.equal(canonical);
    expect(resolvePreviousTimeRange('', now)).to.deep.equal(previous);
    expect(resolvePreviousTimeRange('   ', now)).to.deep.equal(previous);
  });

  it('rejects a key this module does not know', () => {
    expect(() => resolveTimeRange('nonsense', now)).to.throw(
      RangeError,
      /Unrecognized time range key: nonsense/
    );
    expect(() => resolvePreviousTimeRange('nonsense', now)).to.throw(
      RangeError,
      /Unrecognized time range key: nonsense/
    );
  });

  it('resolves the same window for every spelling of 30 days', () => {
    const canonical = resolveTimeRange('last-30', now);
    expect(resolveTimeRange('month', now)).to.deep.equal(canonical);
    expect(resolveTimeRange('30d', now)).to.deep.equal(canonical);
    expect(new Date(canonical.startDate as string).getTime()).to.equal(
      new Date(2026, 7, 7, 14, 30, 0).getTime()
    );
    expect(new Date(canonical.endDate as string).getTime()).to.equal(
      now.getTime()
    );
  });

  it('measures rolling ranges in whole days back from now', () => {
    const day = 24 * 60 * 60 * 1000;
    for (const [key, days] of [
      ['last-24h', 1],
      ['last-7', 7],
      ['last-90', 90],
      ['last-365', 365],
    ] as Array<[string, number]>) {
      const window = resolveTimeRange(key, now);
      const span =
        new Date(window.endDate as string).getTime() -
        new Date(window.startDate as string).getTime();
      // Whole days back from the same instant; DST can move the clock by an
      // hour, which is why this is a tolerance and not an equality.
      expect(Math.abs(span - days * day)).to.be.at.most(day);
    }
  });

  it('runs calendar ranges from the start of the period to now', () => {
    const today = resolveTimeRange('today', now);
    expect(new Date(today.startDate as string).getTime()).to.equal(
      new Date(2026, 8, 6, 0, 0, 0, 0).getTime()
    );

    const thisMonth = resolveTimeRange('this-month', now);
    expect(new Date(thisMonth.startDate as string).getTime()).to.equal(
      new Date(2026, 8, 1, 0, 0, 0, 0).getTime()
    );

    // 6 Sep 2026 is a Sunday, so the week started on 31 Aug.
    const thisWeek = resolveTimeRange('this-week', now);
    expect(new Date(thisWeek.startDate as string).getTime()).to.equal(
      new Date(2026, 7, 31, 0, 0, 0, 0).getTime()
    );

    const lastMonth = resolveTimeRange('last-month', now);
    expect(new Date(lastMonth.startDate as string).getTime()).to.equal(
      new Date(2026, 7, 1, 0, 0, 0, 0).getTime()
    );
    expect(new Date(lastMonth.endDate as string).getTime()).to.equal(
      new Date(2026, 8, 1, 0, 0, 0, 0).getTime()
    );
  });

  it('has no bounds for all time', () => {
    expect(resolveTimeRange('all', now)).to.deep.equal({
      startDate: null,
      endDate: null,
    });
    expect(resolvePreviousTimeRange('all', now)).to.deep.equal({
      startDate: null,
      endDate: null,
    });
  });

  it('puts the prior window immediately before the current one', () => {
    const current = resolveTimeRange('last-30', now);
    const previous = resolvePreviousTimeRange('last-30', now);
    expect(previous.endDate).to.equal(current.startDate);
    const currentSpan =
      new Date(current.endDate as string).getTime() -
      new Date(current.startDate as string).getTime();
    const previousSpan =
      new Date(previous.endDate as string).getTime() -
      new Date(previous.startDate as string).getTime();
    expect(previousSpan).to.equal(currentSpan);

    // A calendar month steps back a whole month, not 30 days.
    const previousMonth = resolvePreviousTimeRange('this-month', now);
    expect(new Date(previousMonth.startDate as string).getTime()).to.equal(
      new Date(2026, 7, 1, 0, 0, 0, 0).getTime()
    );
    expect(previousMonth.endDate).to.equal(
      resolveTimeRange('this-month', now).startDate
    );
  });

  it('runs this year from 1 Jan and compares it with the whole prior year', () => {
    const thisYear = resolveTimeRange('this-year', now);
    expect(new Date(thisYear.startDate as string).getTime()).to.equal(
      new Date(2026, 0, 1, 0, 0, 0, 0).getTime()
    );
    expect(thisYear.endDate).to.equal(now.toISOString());

    const previousYear = resolvePreviousTimeRange('this-year', now);
    expect(new Date(previousYear.startDate as string).getTime()).to.equal(
      new Date(2025, 0, 1, 0, 0, 0, 0).getTime()
    );
    expect(previousYear.endDate).to.equal(thisYear.startDate);
    expect(normalizeTimeRangeKey('this-year')).to.equal('this-year');
    expect(timeRangeShortLabel('this-year')).to.equal('this year');
  });

  it('phrases a window and its comparison so calendar keys read naturally', () => {
    expect(timeRangePhrase('last-30')).to.equal('In the last 30d');
    expect(timeRangePhrase('this-month')).to.equal('This month');
    expect(timeRangePhrase('today')).to.equal('Today');
    expect(timeRangeComparisonLabel('last-30')).to.equal('prior 30d');
    expect(timeRangeComparisonLabel('today')).to.equal('same hours yesterday');
    expect(timeRangeComparisonLabel('this-week')).to.equal('prior week');
    expect(timeRangeComparisonLabel('this-month')).to.equal('prior month');
    expect(timeRangeComparisonLabel('this-year')).to.equal('prior year');
  });

  it('labels a window in the register the stats use', () => {
    expect(timeRangeShortLabel('month')).to.equal('30d');
    expect(timeRangeShortLabel('this-month')).to.equal('this month');
    expect(timeRangeShortLabel('nonsense')).to.equal('30d');
    expect(formatTimeRangeWindow(resolveTimeRange('last-30', now))).to.equal(
      `${new Date(2026, 7, 7).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      })} to ${new Date(2026, 8, 6).toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      })}`
    );
    expect(formatTimeRangeWindow({ startDate: null, endDate: null })).to.equal(
      ''
    );
  });
});
