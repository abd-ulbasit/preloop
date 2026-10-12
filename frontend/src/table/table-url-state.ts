/**
 * Sort, search, filters and page in the query string.
 *
 * A list someone sorted, filtered and paged is a view worth linking, and the
 * back button should not lose it. The shape is short and readable on purpose:
 *
 *   ?sort=-created,name&q=deploy&f.status=failed&f.status=paused&page=3&size=50
 *
 * `sort` is comma-separated ids, `-` meaning descending. `page` is 1-based,
 * the way a person counts. Defaults are left out so a fresh list has a clean
 * URL. Writes use `replaceState` through `replaceListFilters`, which keeps
 * unrelated parameters and the hash intact.
 */
import { replaceListFilters } from '../utils/list-filter-url';

export interface TableUrlOptions {
  /** Prefix for every parameter, when two lists share one page. */
  prefix?: string;
}

export interface TableUrlState {
  sorting?: Array<{ id: string; desc: boolean }>;
  search?: string;
  filters: Record<string, string | string[]>;
  /** 0-based. */
  pageIndex?: number;
  pageSize?: number;
}

const MAX_PAGE_SIZE = 500;

function keys(options: TableUrlOptions) {
  const prefix = options.prefix ? `${options.prefix}.` : '';
  return {
    sort: `${prefix}sort`,
    search: `${prefix}q`,
    page: `${prefix}page`,
    size: `${prefix}size`,
    filter: `${prefix}f.`,
  };
}

function positiveInt(raw: string | null): number | undefined {
  if (!raw || !/^\d{1,6}$/.test(raw)) return undefined;
  const value = Number(raw);
  return value > 0 ? value : undefined;
}

export function readTableUrlState(
  options: TableUrlOptions,
  search: string = window.location.search
): TableUrlState {
  const params = new URLSearchParams(search);
  const k = keys(options);
  const state: TableUrlState = { filters: {} };
  const sort = params.get(k.sort);
  if (sort) {
    state.sorting = sort
      .split(',')
      .map((part) => part.trim())
      .filter((part) => /^-?[A-Za-z0-9_.:-]+$/.test(part))
      .map((part) =>
        part.startsWith('-')
          ? { id: part.slice(1), desc: true }
          : { id: part, desc: false }
      );
  }
  const q = params.get(k.search);
  if (q) state.search = q;
  const page = positiveInt(params.get(k.page));
  if (page) state.pageIndex = page - 1;
  const size = positiveInt(params.get(k.size));
  if (size && size <= MAX_PAGE_SIZE) state.pageSize = size;
  for (const key of new Set(params.keys())) {
    if (!key.startsWith(k.filter)) continue;
    const id = key.slice(k.filter.length);
    const values = params.getAll(key).filter(Boolean);
    if (!id || !values.length) continue;
    state.filters[id] = values.length === 1 ? values[0] : values;
  }
  return state;
}

export function writeTableUrlState(
  options: TableUrlOptions,
  state: Required<Omit<TableUrlState, 'filters'>> & {
    filters: Record<string, string | string[]>;
  }
): void {
  const k = keys(options);
  const updates: Record<string, string | string[] | null> = {
    [k.sort]: state.sorting.length
      ? state.sorting.map((s) => (s.desc ? `-${s.id}` : s.id)).join(',')
      : null,
    [k.search]: state.search || null,
    [k.page]: state.pageIndex > 0 ? String(state.pageIndex + 1) : null,
    [k.size]: null,
  };
  // Filters that were cleared must leave the URL too, so drop every filter
  // parameter currently there before writing the live ones.
  const current = new URLSearchParams(window.location.search);
  for (const key of current.keys()) {
    if (key.startsWith(k.filter)) updates[key] = null;
  }
  for (const [id, value] of Object.entries(state.filters)) {
    updates[`${k.filter}${id}`] = value;
  }
  if (state.pageSize) {
    // Only written when it is not what the list would pick on its own is
    // not knowable here, so the size is written whenever a list pages: a
    // link should reproduce the page someone saw.
    updates[k.size] = String(state.pageSize);
  }
  replaceListFilters(updates);
}
