/**
 * The Notes tab on an agent page: a compact box to write one, and the notes
 * already sent, newest first, one line each.
 *
 * A busy agent collects dozens of long notes. Rendered in full above the tabs
 * they pushed the agent's sessions off the first screen, so here each note is
 * a one-line preview that expands on click, the first page is three notes,
 * and older ones load on demand.
 */
import { LitElement, css, html, nothing } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

import '@shoelace-style/shoelace/dist/components/button/button.js';
import '@shoelace-style/shoelace/dist/components/icon/icon.js';

import { cancelOperatorNote, listOperatorNotesPage } from '../api';
import type { OperatorNote } from '../types';
import {
  noteAuthorIcon,
  noteAuthorKind,
  noteAuthorName,
} from '../utils/note-author';
import { noteStateLabel } from './operator-note-composer';
import './operator-note-composer';

/** Notes shown before the operator asks for more. */
export const NOTES_FIRST_PAGE = 3;
/** Notes per "Show older" click. */
export const NOTES_OLDER_PAGE = 20;

@customElement('agent-notes-tab')
export class AgentNotesTab extends LitElement {
  @property({ type: String, attribute: 'agent-id' })
  agentId = '';

  /** False for an agent no note can reach: the list stays, the box goes. */
  @property({ type: Boolean, attribute: 'can-send' })
  canSend = false;

  /** First page, when the host already read it (for the tab's count). */
  @property({ attribute: false })
  initial: { notes: OperatorNote[]; total: number } | null = null;

  @state() private notes: OperatorNote[] = [];
  @state() private total = 0;
  @state() private loading = false;
  @state() private error: string | null = null;
  @state() private expanded = new Set<string>();

  private loadedFor = '';

  static styles = css`
    :host {
      display: block;
    }

    .composer {
      margin-bottom: var(--sl-spacing-medium);
    }

    ul {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    li {
      border-top: 1px solid var(--console-hairline, var(--sl-color-neutral-200));
    }

    .row {
      align-items: baseline;
      background: none;
      border: 0;
      color: inherit;
      cursor: pointer;
      display: grid;
      font: inherit;
      gap: var(--sl-spacing-small);
      grid-template-columns: auto minmax(0, 1fr) auto;
      padding: var(--sl-spacing-x-small) 0;
      text-align: left;
      width: 100%;
    }

    .row:focus-visible {
      outline: 2px solid var(--sl-color-primary-600);
      outline-offset: 2px;
    }

    .author {
      align-items: center;
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      display: inline-flex;
      font-size: var(--console-text-meta, 13px);
      font-weight: 600;
      gap: var(--sl-spacing-3x-small);
      white-space: nowrap;
    }

    .preview {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .meta {
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      font-size: var(--console-text-meta, 13px);
      white-space: nowrap;
    }

    .state.pending {
      color: var(--sl-color-warning-700);
    }

    .state.delivered,
    .state.acknowledged {
      color: var(--sl-color-success-700);
    }

    .state.undeliverable,
    .state.failed {
      color: var(--sl-color-danger-700, #b91c1c);
    }

    .full {
      padding: 0 0 var(--sl-spacing-small);
    }

    .full .text {
      overflow-wrap: anywhere;
      white-space: pre-wrap;
    }

    .full .times {
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      font-size: var(--console-text-meta, 13px);
      margin-top: var(--sl-spacing-2x-small);
    }

    .more {
      align-items: center;
      display: flex;
      gap: var(--sl-spacing-small);
      margin-top: var(--sl-spacing-small);
    }

    .empty,
    .error {
      color: var(--console-meta-color, var(--sl-color-neutral-600));
      font-size: var(--console-text-meta, 13px);
      padding: var(--sl-spacing-x-small) 0;
    }

    .error {
      color: var(--sl-color-danger-600);
    }
  `;

  protected willUpdate(changed: Map<string, unknown>): void {
    // Seed once per agent. A later page object from the host (for example
    // its count after a send) must not replace notes this tab added, paged
    // or expanded itself.
    if (
      changed.has('initial') &&
      this.initial &&
      this.loadedFor !== this.agentId
    ) {
      this.notes = this.initial.notes;
      this.total = this.initial.total;
      this.loadedFor = this.agentId;
    }
  }

  protected updated(changed: Map<string, unknown>): void {
    if (
      changed.has('agentId') &&
      this.agentId &&
      this.loadedFor !== this.agentId
    ) {
      void this.load(true);
    }
  }

  /** Focus the box, for the header's "Note" action. */
  focusComposer(): void {
    const composer = this.renderRoot.querySelector('operator-note-composer');
    const input =
      composer?.shadowRoot?.querySelector<HTMLElement>('sl-textarea');
    input?.focus();
  }

  private async load(reset: boolean): Promise<void> {
    const agentId = this.agentId;
    if (!agentId || this.loading) return;
    this.loading = true;
    this.error = null;
    try {
      const page = await listOperatorNotesPage({
        agentId,
        limit: reset ? NOTES_FIRST_PAGE : NOTES_OLDER_PAGE,
        offset: reset ? 0 : this.notes.length,
      });
      if (agentId !== this.agentId) return;
      this.loadedFor = agentId;
      this.notes = reset ? page.notes : [...this.notes, ...page.notes];
      this.total = page.total;
    } catch (error) {
      this.error =
        error instanceof Error ? error.message : 'Could not load the notes';
    } finally {
      this.loading = false;
    }
  }

  private onSent(event: CustomEvent<OperatorNote>): void {
    const note = event.detail;
    if (!note?.note_id) return;
    this.notes = [
      note,
      ...this.notes.filter((n) => n.note_id !== note.note_id),
    ];
    this.total += 1;
    this.dispatchEvent(
      new CustomEvent('notes-count-changed', {
        detail: { total: this.total },
        bubbles: true,
        composed: true,
      })
    );
  }

  private toggle(noteId: string): void {
    const next = new Set(this.expanded);
    if (next.has(noteId)) next.delete(noteId);
    else next.add(noteId);
    this.expanded = next;
  }

  private async withdraw(note: OperatorNote): Promise<void> {
    try {
      const updated = await cancelOperatorNote(note.note_id);
      this.notes = this.notes.map((n) =>
        n.note_id === updated.note_id ? updated : n
      );
    } catch (error) {
      this.error =
        error instanceof Error ? error.message : 'Failed to withdraw the note';
    }
  }

  private renderTimes(note: OperatorNote) {
    const times: Array<[string, string | null | undefined]> = [
      ['Created', note.created_at],
      ['Claimed', note.claimed_at],
      ['Delivered', note.delivered_at],
      ['Expired', note.expired_at],
      ['Withdrawn', note.cancelled_at],
      ['Undeliverable', note.undeliverable_at],
    ];
    return times
      .filter(([, at]) => at)
      .map(
        ([label, at], index) =>
          html`${index ? ' · ' : ''}${label}
            <time datetime=${at!}>${new Date(at!).toLocaleString()}</time>`
      );
  }

  private renderNote(note: OperatorNote) {
    const open = this.expanded.has(note.note_id);
    const kind = noteAuthorKind(note.author?.auth_method);
    const when = note.delivered_at || note.created_at;
    return html`<li data-testid="agent-note-${note.note_id}">
      <button
        class="row"
        type="button"
        aria-expanded=${open ? 'true' : 'false'}
        @click=${() => this.toggle(note.note_id)}
      >
        <span class="author" data-author-kind=${kind}>
          <sl-icon name=${noteAuthorIcon(note.author?.auth_method)}></sl-icon>
          ${noteAuthorName(note.author?.display)}
        </span>
        <span class="preview">${note.text}</span>
        <span class="meta">
          <span class="state ${note.state}">${noteStateLabel(note)}</span>
          ${when ? html` · ${new Date(when).toLocaleDateString()}` : nothing}
        </span>
      </button>
      ${
        open
          ? html`<div class="full" data-testid="agent-note-full">
              <div class="text">${note.text}</div>
              <div class="times">
                ${this.renderTimes(note)}
                ${
                  note.state === 'pending'
                    ? html`<sl-button
                        size="small"
                        variant="text"
                        @click=${() => void this.withdraw(note)}
                        >Withdraw</sl-button
                      >`
                    : nothing
                }
              </div>
            </div>`
          : nothing
      }
    </li>`;
  }

  render() {
    const remaining = Math.max(0, this.total - this.notes.length);
    return html`
      ${
        this.canSend
          ? html`<div class="composer">
              <operator-note-composer
                agent-id=${this.agentId}
                no-history
                @operator-note-sent=${(event: CustomEvent<OperatorNote>) =>
                  this.onSent(event)}
              ></operator-note-composer>
            </div>`
          : html`<div class="empty" data-testid="notes-unsupported">
              No note can reach this agent: it sends no model traffic through
              the gateway and has no note hook. Earlier notes stay listed here.
            </div>`
      }
      ${this.error ? html`<div class="error" role="alert">${this.error}</div>` : nothing}
      ${
        this.notes.length
          ? html`<ul data-testid="agent-notes-list">
              ${this.notes.map((note) => this.renderNote(note))}
            </ul>`
          : this.loading
            ? nothing
            : html`<div class="empty">No notes yet.</div>`
      }
      ${
        remaining > 0
          ? html`<div class="more">
              <sl-button
                size="small"
                ?loading=${this.loading}
                data-testid="notes-older"
                @click=${() => void this.load(false)}
                >Show older notes (${remaining})</sl-button
              >
            </div>`
          : nothing
      }
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    'agent-notes-tab': AgentNotesTab;
  }
}
