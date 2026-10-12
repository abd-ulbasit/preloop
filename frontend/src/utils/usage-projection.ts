/**
 * The Usage card's projection for a to-date range (#1610).
 *
 * "This month" on the 12th drew twelve days and stopped, which read as a
 * chart that had not finished loading. The card now spans the whole period
 * and says what the rest of it probably looks like, drawn so it cannot be
 * mistaken for data.
 *
 * Magnitude comes from `budgetForecast`, unchanged: what has been used so
 * far divided by the fraction of the period gone. That is the same number
 * the budget row prints as "On track for $X", so the card never shows two
 * different projected totals. Because the rate is per unit of elapsed time,
 * the partial current bucket counts for the time it has actually covered,
 * not as a whole bucket.
 *
 * Shape is only how that remaining amount is spread over the remaining
 * buckets: weekday means for a week or a month, hour-of-day means for today,
 * flat for a year or when there is too little history to have a pattern.
 */
import type { GatewayUsageByDay } from '../types';
import { budgetForecast, MIN_ELAPSED_FRACTION } from './budget-forecast';
import {
  normalizeTimeRangeKey,
  resolveTimeRange,
  type TimeRangeKey,
} from './time-range';

export type UsageSeriesGranularity = 'hour' | 'day' | 'month';
export type ProjectionShape = 'weekday' | 'hour-of-day' | 'flat';

/** Weekday and hour-of-day shapes need this much history to mean anything. */
export const MIN_WEEKDAY_HISTORY_DAYS = 14;
/** The hourly series is capped at 7 days, so that is all a shape can use. */
export const MIN_HOURLY_HISTORY_DAYS = 7;

const HOUR_MS = 3_600_000;
const DAY_MS = 24 * HOUR_MS;

const PROJECTED_RANGES: Partial<Record<TimeRangeKey, string>> = {
  today: 'daily',
  'this-week': 'weekly',
  'this-month': 'monthly',
  'this-year': 'yearly',
};

/** True for the to-date ranges that get a full-period chart. */
export function isProjectedRange(key: string): boolean {
  const normalized = normalizeTimeRangeKey(key);
  return normalized != null && normalized in PROJECTED_RANGES;
}

/** The budget period a to-date range lines up with, or null. */
export function projectionPeriodFor(key: string): string | null {
  const normalized = normalizeTimeRangeKey(key);
  return (normalized && PROJECTED_RANGES[normalized]) || null;
}

/** Bucket size for a range's chart. */
export function usageSeriesGranularity(key: string): UsageSeriesGranularity {
  const normalized = normalizeTimeRangeKey(key);
  if (normalized === 'today') return 'hour';
  if (normalized === 'this-year') return 'month';
  return 'day';
}

/**
 * Where the current period starts and ends (exclusive). Calendar ranges run
 * to the end of the period, not to now.
 */
export function usagePeriodBounds(
  key: string,
  now: Date = new Date()
): { start: Date; end: Date } | null {
  const normalized = normalizeTimeRangeKey(key);
  if (!normalized || normalized === 'all') return null;
  const window = resolveTimeRange(normalized, now);
  const start = new Date(window.startDate as string);
  if (!isProjectedRange(normalized)) {
    return { start, end: new Date(window.endDate as string) };
  }
  const end = new Date(start.getTime());
  if (normalized === 'today') end.setDate(end.getDate() + 1);
  else if (normalized === 'this-week') end.setDate(end.getDate() + 7);
  else if (normalized === 'this-month') end.setMonth(end.getMonth() + 1);
  else end.setFullYear(end.getFullYear() + 1);
  return { start, end };
}

/**
 * The one `/cost/timeseries` request the card makes for a range: the
 * current window plus, where a shape uses it, the history before it (the
 * prior period for a week or a month, the prior 7 days for today).
 */
export function usageSeriesRequest(
  key: string,
  now: Date = new Date()
): {
  startDate: string;
  endDate: string;
  granularity: UsageSeriesGranularity;
} | null {
  const normalized = normalizeTimeRangeKey(key);
  if (!normalized || normalized === 'all') return null;
  const window = resolveTimeRange(normalized, now);
  const granularity = usageSeriesGranularity(normalized);
  let start = new Date(window.startDate as string);
  if (normalized === 'today') {
    // The server caps hourly windows at exactly 7 days.
    start = new Date(now.getTime() - 7 * DAY_MS);
  } else if (normalized === 'this-week') {
    start = new Date(start.getTime());
    start.setDate(start.getDate() - 7);
  } else if (normalized === 'this-month') {
    start = new Date(start.getTime());
    start.setMonth(start.getMonth() - 1);
  }
  return {
    startDate: start.toISOString(),
    endDate: now.toISOString(),
    granularity,
  };
}

/** Start of the UTC bucket containing `date`. */
export function bucketStart(
  date: Date,
  granularity: UsageSeriesGranularity
): Date {
  const out = new Date(date.getTime());
  out.setUTCMinutes(0, 0, 0);
  if (granularity === 'hour') return out;
  out.setUTCHours(0);
  if (granularity === 'day') return out;
  out.setUTCDate(1);
  return out;
}

function nextBucket(date: Date, granularity: UsageSeriesGranularity): Date {
  const out = new Date(date.getTime());
  if (granularity === 'hour') out.setUTCHours(out.getUTCHours() + 1);
  else if (granularity === 'day') out.setUTCDate(out.getUTCDate() + 1);
  else out.setUTCMonth(out.getUTCMonth() + 1);
  return out;
}

/** The key the server uses for a bucket (`date` in the timeseries rows). */
export function bucketKey(
  date: Date,
  granularity: UsageSeriesGranularity
): string {
  const iso = date.toISOString();
  if (granularity === 'hour') return `${iso.slice(0, 13)}:00:00Z`;
  return iso.slice(0, 10);
}

/** Normalise a server bucket date (hour rows may carry an offset). */
function rowKey(raw: string, granularity: UsageSeriesGranularity): string {
  if (granularity !== 'hour') return raw.slice(0, 10);
  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? raw : bucketKey(parsed, 'hour');
}

/** Bucket value in the unit the card shows, keyed by bucket start. */
export function seriesValues(
  rows: GatewayUsageByDay[],
  granularity: UsageSeriesGranularity,
  unit: 'tokens' | 'dollars'
): Map<string, number> {
  const values = new Map<string, number>();
  for (const row of rows) {
    const key = rowKey(row.date, granularity);
    const value =
      unit === 'dollars'
        ? Number(row.estimated_cost || 0)
        : Number(row.total_tokens || 0);
    values.set(key, (values.get(key) || 0) + value);
  }
  return values;
}

export interface UsageChartSlot {
  key: string;
  /** Used so far; null after now. The current bucket holds its partial value. */
  actual: number | null;
  /** Expected bucket total from now on; null before the current bucket. */
  projected: number | null;
}

export type UsageProjectionStatus = 'projected' | 'too-early' | 'none';

export interface UsageProjection {
  slots: UsageChartSlot[];
  /** Index of the bucket that contains now. */
  currentIndex: number;
  status: UsageProjectionStatus;
  shape: ProjectionShape;
  /** Projected total at the end of the period (equals the budget forecast). */
  projectedTotal: number | null;
  /** Share of the remaining amount per remaining bucket; sums to 1. */
  shares: number[];
  /** Exclusive end of the period. */
  end: Date;
}

export interface UsageProjectionInput {
  range: string;
  /** Total so far for the period, from the summary the headline shows. */
  totalSoFar: number;
  /** Bucket values (current period and any history before it). */
  values: Map<string, number>;
  granularity: UsageSeriesGranularity;
  now?: Date;
}

/**
 * Mean per pattern slot (weekday 0..6 or UTC hour 0..23) over the complete
 * buckets from the first bucket with usage up to the current one. Missing
 * buckets count as zero: the server only returns buckets that had usage.
 */
function patternWeights(
  values: Map<string, number>,
  granularity: 'hour' | 'day',
  current: Date,
  minHistoryDays: number
): number[] | null {
  const keys = [...values.entries()]
    .filter(([, value]) => value > 0)
    .map(([key]) => key)
    .sort();
  if (keys.length === 0) return null;
  const first = new Date(
    granularity === 'hour' ? keys[0] : `${keys[0]}T00:00:00Z`
  );
  if (Number.isNaN(first.getTime())) return null;
  if ((current.getTime() - first.getTime()) / DAY_MS < minHistoryDays) {
    return null;
  }
  const size = granularity === 'hour' ? 24 : 7;
  const sums = new Array<number>(size).fill(0);
  const counts = new Array<number>(size).fill(0);
  for (
    let cursor = first;
    cursor.getTime() < current.getTime();
    cursor = nextBucket(cursor, granularity)
  ) {
    const slot =
      granularity === 'hour' ? cursor.getUTCHours() : cursor.getUTCDay();
    sums[slot] += values.get(bucketKey(cursor, granularity)) || 0;
    counts[slot] += 1;
  }
  const means = sums.map((sum, index) =>
    counts[index] > 0 ? sum / counts[index] : NaN
  );
  const known = means.filter((mean) => !Number.isNaN(mean));
  const overall = known.reduce((a, b) => a + b, 0) / (known.length || 1);
  if (overall <= 0) return null;
  return means.map((mean) => (Number.isNaN(mean) ? overall : mean));
}

/**
 * Full-period slots with the actual series up to now and the projection
 * after it. Rolling ranges get no projection: their slots are the window.
 */
export function buildUsageProjection(
  input: UsageProjectionInput
): UsageProjection | null {
  const now = input.now ?? new Date();
  const bounds = usagePeriodBounds(input.range, now);
  if (!bounds) return null;
  const { granularity, values } = input;
  const period = projectionPeriodFor(input.range);

  const slotStarts: Date[] = [];
  for (
    let cursor = bucketStart(bounds.start, granularity);
    cursor.getTime() < bounds.end.getTime();
    cursor = nextBucket(cursor, granularity)
  ) {
    slotStarts.push(cursor);
  }
  const current = bucketStart(now, granularity);
  let currentIndex = slotStarts.findIndex(
    (start) => start.getTime() === current.getTime()
  );
  if (currentIndex < 0) currentIndex = slotStarts.length - 1;

  const empty = {
    shares: [] as number[],
    projectedTotal: null,
    shape: 'flat' as ProjectionShape,
    end: bounds.end,
  };
  if (!period) {
    return {
      ...empty,
      slots: slotStarts.map((start) => ({
        key: bucketKey(start, granularity),
        actual: values.get(bucketKey(start, granularity)) || 0,
        projected: null,
      })),
      currentIndex: slotStarts.length - 1,
      status: 'none',
    };
  }

  const actualSlots = (): UsageChartSlot[] =>
    slotStarts.map((start, index) => ({
      key: bucketKey(start, granularity),
      actual:
        index <= currentIndex
          ? values.get(bucketKey(start, granularity)) || 0
          : null,
      projected: null,
    }));

  const span = bounds.end.getTime() - bounds.start.getTime();
  const elapsedFraction = (now.getTime() - bounds.start.getTime()) / span;
  if (elapsedFraction < MIN_ELAPSED_FRACTION) {
    return {
      ...empty,
      slots: actualSlots(),
      currentIndex,
      status: 'too-early',
    };
  }

  const totalSoFar = Math.max(0, Number(input.totalSoFar || 0));
  const forecast =
    totalSoFar > 0
      ? budgetForecast({
          period,
          spend: totalSoFar,
          periodStart: bounds.start.toISOString(),
          periodEnd: bounds.end.toISOString(),
          now,
        })
      : null;
  const projectedTotal = forecast ? forecast.amount : totalSoFar;
  const remaining = Math.max(0, projectedTotal - totalSoFar);

  let shape: ProjectionShape = 'flat';
  let weights: number[] | null = null;
  if (granularity === 'day') {
    weights = patternWeights(values, 'day', current, MIN_WEEKDAY_HISTORY_DAYS);
    if (weights) shape = 'weekday';
  } else if (granularity === 'hour') {
    weights = patternWeights(values, 'hour', current, MIN_HOURLY_HISTORY_DAYS);
    if (weights) shape = 'hour-of-day';
  }

  // Each remaining bucket's share is its pattern weight times the time it
  // has left inside the period (the rest of the current bucket, all of the
  // later ones, clipped to the period bounds).
  const raw = slotStarts.slice(currentIndex).map((start) => {
    const bucketEnd = Math.min(
      nextBucket(start, granularity).getTime(),
      bounds.end.getTime()
    );
    const from = Math.max(start.getTime(), now.getTime());
    const length = Math.max(0, bucketEnd - from);
    const slot =
      granularity === 'hour' ? start.getUTCHours() : start.getUTCDay();
    return (weights ? weights[slot] : 1) * length;
  });
  const rawTotal = raw.reduce((a, b) => a + b, 0);
  const shares =
    rawTotal > 0 ? raw.map((value) => value / rawTotal) : raw.map(() => 0);

  const slots = actualSlots();
  shares.forEach((share, offset) => {
    const index = currentIndex + offset;
    const base = offset === 0 ? slots[index].actual || 0 : 0;
    slots[index].projected = base + remaining * share;
  });

  return {
    slots,
    currentIndex,
    status: 'projected',
    shape,
    projectedTotal,
    shares,
    end: bounds.end,
  };
}
