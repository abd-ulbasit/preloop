import { expect } from '@open-wc/testing';

import { budgetForecast } from './budget-forecast';
import {
  buildUsageProjection,
  usagePeriodBounds,
  usageSeriesRequest,
  seriesValues,
} from './usage-projection';

/** Day-bucket values keyed like the server sends them. */
function days(
  from: string,
  count: number,
  value: (date: Date) => number
): Map<string, number> {
  const out = new Map<string, number>();
  const start = new Date(`${from}T00:00:00Z`);
  for (let i = 0; i < count; i += 1) {
    const date = new Date(start.getTime() + i * 86_400_000);
    out.set(date.toISOString().slice(0, 10), value(date));
  }
  return out;
}

function sum(values: number[]): number {
  return values.reduce((a, b) => a + b, 0);
}

describe('usage-projection', () => {
  const now = new Date('2026-10-15T12:00:00');

  it('spans the whole month and ends on the linear forecast', () => {
    const values = days('2026-09-01', 45, () => 10);
    const projection = buildUsageProjection({
      range: 'this-month',
      totalSoFar: 145,
      values,
      granularity: 'day',
      now,
    })!;
    expect(projection.status).to.equal('projected');
    expect(projection.slots.length).to.be.within(31, 32);
    const bounds = usagePeriodBounds('this-month', now)!;
    const forecast = budgetForecast({
      period: 'monthly',
      spend: 145,
      periodStart: bounds.start.toISOString(),
      periodEnd: bounds.end.toISOString(),
      now,
    })!;
    expect(projection.projectedTotal).to.be.closeTo(forecast.amount, 1e-9);
    const current = projection.slots[projection.currentIndex];
    const projectedRest =
      sum(
        projection.slots
          .slice(projection.currentIndex)
          .map((slot) => slot.projected || 0)
      ) - (current.actual || 0);
    expect(145 + projectedRest).to.be.closeTo(forecast.amount, 1e-6);
  });

  it('remaining shares sum to one', () => {
    const projection = buildUsageProjection({
      range: 'this-month',
      totalSoFar: 145,
      values: days('2026-09-01', 45, (d) => (d.getUTCDay() === 0 ? 1 : 20)),
      granularity: 'day',
      now,
    })!;
    expect(sum(projection.shares)).to.be.closeTo(1, 1e-9);
    expect(projection.shape).to.equal('weekday');
    // Sundays are quiet in the history, so a future Sunday gets less.
    const sunday = projection.slots.find(
      (slot, i) =>
        i > projection.currentIndex &&
        new Date(`${slot.key}T00:00:00Z`).getUTCDay() === 0
    )!;
    const monday = projection.slots.find(
      (slot, i) =>
        i > projection.currentIndex &&
        new Date(`${slot.key}T00:00:00Z`).getUTCDay() === 1
    )!;
    expect(sunday.projected!).to.be.lessThan(monday.projected!);
  });

  it('falls back to flat with under 14 days of history', () => {
    const projection = buildUsageProjection({
      range: 'this-month',
      totalSoFar: 50,
      values: days('2026-10-10', 6, (d) => (d.getUTCDay() === 0 ? 1 : 20)),
      granularity: 'day',
      now,
    })!;
    expect(projection.shape).to.equal('flat');
    const future = projection.slots
      .slice(projection.currentIndex + 1, -1)
      .map((slot) => slot.projected!);
    future.forEach((value) => expect(value).to.be.closeTo(future[0], 1e-9));
  });

  it('draws no projection too early in the period', () => {
    const projection = buildUsageProjection({
      range: 'this-month',
      totalSoFar: 50,
      values: days('2026-10-01', 2, () => 25),
      granularity: 'day',
      now: new Date('2026-10-02T09:00:00'),
    })!;
    expect(projection.status).to.equal('too-early');
    expect(projection.projectedTotal).to.equal(null);
    expect(projection.slots.every((slot) => slot.projected === null)).to.equal(
      true
    );
  });

  it('counts the partial current bucket by elapsed time, not as a whole bucket', () => {
    // 10 per full day for 14 days, and the 15th is half gone with 5 so far.
    // A per-bucket rate would read 145 / 15 days; the time rate is
    // 145 / 14.5 days = 10 per day, which is the truth.
    const values = days('2026-10-01', 15, (d) =>
      d.getUTCDate() === 15 ? 5 : 10
    );
    const local = new Date('2026-10-15T12:00:00');
    const projection = buildUsageProjection({
      range: 'this-month',
      totalSoFar: 145,
      values,
      granularity: 'day',
      now: local,
    })!;
    const bounds = usagePeriodBounds('this-month', local)!;
    const elapsedDays = (local.getTime() - bounds.start.getTime()) / 86_400_000;
    const periodDays =
      (bounds.end.getTime() - bounds.start.getTime()) / 86_400_000;
    expect(projection.projectedTotal).to.be.closeTo(
      (145 / elapsedDays) * periodDays,
      1e-6
    );
  });

  it('keeps rolling ranges unprojected', () => {
    const projection = buildUsageProjection({
      range: 'last-30',
      totalSoFar: 100,
      values: days('2026-09-15', 31, () => 3),
      granularity: 'day',
      now,
    })!;
    expect(projection.status).to.equal('none');
    expect(projection.slots.every((slot) => slot.projected === null)).to.equal(
      true
    );
  });

  it('projects a year flat per remaining month', () => {
    const projection = buildUsageProjection({
      range: 'this-year',
      totalSoFar: 900,
      values: new Map([['2026-10-01', 100]]),
      granularity: 'month',
      now,
    })!;
    expect(projection.shape).to.equal('flat');
    expect(projection.slots.length).to.be.within(12, 13);
  });

  it('shapes today by hour of day from the prior week', () => {
    const values = new Map<string, number>();
    const today = new Date('2026-10-15T15:30:00Z');
    for (let h = 0; h < 8 * 24; h += 1) {
      const t = new Date(today.getTime() - h * 3_600_000);
      const key = `${t.toISOString().slice(0, 13)}:00:00Z`;
      values.set(key, t.getUTCHours() >= 9 && t.getUTCHours() < 17 ? 10 : 1);
    }
    const projection = buildUsageProjection({
      range: 'today',
      totalSoFar: 80,
      values,
      granularity: 'hour',
      now: today,
    })!;
    expect(projection.slots.length).to.be.within(23, 26);
    if (projection.status === 'projected') {
      expect(projection.shape).to.equal('hour-of-day');
      expect(sum(projection.shares)).to.be.closeTo(1, 1e-9);
    }
  });

  it('asks for the prior period and caps today at 7 days of hours', () => {
    const month = usageSeriesRequest('this-month', now)!;
    expect(month.granularity).to.equal('day');
    expect(new Date(month.startDate).getMonth()).to.equal(8);
    const today = usageSeriesRequest('today', now)!;
    expect(today.granularity).to.equal('hour');
    expect(
      new Date(today.endDate).getTime() - new Date(today.startDate).getTime()
    ).to.equal(7 * 86_400_000);
    expect(usageSeriesRequest('this-year', now)!.granularity).to.equal('month');
  });

  it('reads hour rows with any offset spelling', () => {
    const values = seriesValues(
      [
        {
          date: '2026-10-15T10:00:00+00:00',
          estimated_cost: 2,
          total_tokens: 5,
        } as never,
      ],
      'hour',
      'dollars'
    );
    expect(values.get('2026-10-15T10:00:00Z')).to.equal(2);
  });
});
