import { html, nothing } from 'lit';
import type {
  ReactiveController,
  ReactiveControllerHost,
  TemplateResult,
} from 'lit';
import type { SessionPage } from '../api';
import type { GatewayUsageBySession } from '../types';
import '../table/table-pagination';

/** What one fetch of a session page returns. */
export interface SessionPageResult {
  usage_by_session: GatewayUsageBySession[];
  total_sessions?: number | null;
}

/** Sessions per page; a page holds every model row of its sessions. */
export const SESSION_PAGE_SIZE = 25;

/**
 * Server pages of the gateway session breakdown (#1585).
 *
 * The summary used to return the 250 most recent session rows and the
 * console paged those in the browser, so session 251 was out of reach. The
 * server now pages by session; this keeps the page, the total (stated on
 * the first page and kept for the next ones) and drops a slower answer to an
 * older request. Rows of the page still sort and search in the browser
 * (`ListTable` client mode), which is what the column headers say.
 */
export class SessionPages implements ReactiveController {
  rows: GatewayUsageBySession[] = [];
  pageIndex = 0;
  pageSize = SESSION_PAGE_SIZE;
  /** Sessions in the period, or null until the first page states it. */
  total: number | null = null;
  loading = false;
  error: string | null = null;
  /** True once a page has landed for the current filter. */
  loaded = false;
  private generation = 0;

  constructor(
    private readonly host: ReactiveControllerHost,
    private readonly fetchPage: (
      page: SessionPage
    ) => Promise<SessionPageResult>,
    pageSize = SESSION_PAGE_SIZE
  ) {
    this.pageSize = pageSize;
    host.addController(this);
  }

  hostConnected(): void {}

  hostDisconnected(): void {
    // A page landing after the view left must not repaint it.
    this.generation++;
  }

  /** A new period or filter: back to page one, total recounted. */
  reset(): Promise<void> {
    this.pageIndex = 0;
    this.total = null;
    this.loaded = false;
    this.rows = [];
    return this.load();
  }

  goTo(pageIndex: number): Promise<void> {
    if (pageIndex < 0 || pageIndex === this.pageIndex) return Promise.resolve();
    this.pageIndex = pageIndex;
    return this.load();
  }

  setPageSize(pageSize: number): Promise<void> {
    if (pageSize === this.pageSize) return Promise.resolve();
    this.pageSize = pageSize;
    this.pageIndex = 0;
    this.total = null;
    return this.load();
  }

  async load(): Promise<void> {
    const generation = ++this.generation;
    this.loading = true;
    this.error = null;
    this.host.requestUpdate();
    try {
      const result = await this.fetchPage({
        limit: this.pageSize,
        offset: this.pageIndex * this.pageSize,
      });
      if (generation !== this.generation) return;
      this.rows = result.usage_by_session ?? [];
      if (typeof result.total_sessions === 'number') {
        this.total = result.total_sessions;
      }
      this.loaded = true;
    } catch (error) {
      if (generation !== this.generation) return;
      this.error =
        error instanceof Error ? error.message : 'Failed to load sessions';
    } finally {
      if (generation === this.generation) {
        this.loading = false;
        this.host.requestUpdate();
      }
    }
  }

  /**
   * Sessions on this page, counted the way the server pages them: a runtime
   * session and flow execution pair, which can span several model rows.
   */
  get sessionsOnPage(): number {
    return new Set(
      this.rows.map((row) =>
        row.runtime_session_id || row.flow_execution_id
          ? `${row.runtime_session_id ?? ''}|${row.flow_execution_id ?? ''}`
          : `${row.session_source_type ?? ''}:${row.session_source_id ?? ''}`
      )
    ).size;
  }

  /** "1,204 sessions" once the total is known. */
  get countLabel(): string {
    const total = this.total;
    if (total === null) return '';
    return `${total.toLocaleString()} ${total === 1 ? 'session' : 'sessions'}`;
  }

  /** The shared pager, counting sessions, not rows. */
  renderPagination(): TemplateResult | typeof nothing {
    if (this.error) return nothing;
    const hasNext = this.total === null && this.sessionsOnPage >= this.pageSize;
    return html`<table-pagination
      class="session-pager"
      page-index=${this.pageIndex}
      page-size=${this.pageSize}
      .pageSizes=${[10, 25, 50]}
      .total=${this.total}
      page-rows=${this.sessionsOnPage}
      ?has-next=${hasNext}
      ?loading=${this.loading}
      noun="sessions"
      @page-change=${(event: CustomEvent<{ pageIndex: number }>) =>
        void this.goTo(event.detail.pageIndex)}
      @page-size-change=${(event: CustomEvent<{ pageSize: number }>) =>
        void this.setPageSize(event.detail.pageSize)}
    ></table-pagination>`;
  }
}
