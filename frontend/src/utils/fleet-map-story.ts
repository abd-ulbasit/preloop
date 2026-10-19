import type {
  FleetMapEdge,
  FleetMapGraph,
  FleetMapNode,
} from '../fleet-map-api';
import {
  object,
  safeEventText as text,
  type FleetMessage,
} from './fleet-map-motion';
import { parseUTCDate } from './date';

type Work = { node: FleetMapNode; at: number; expires?: number };
type Run = {
  flow: string;
  name: string;
  status: string;
  at: number;
  end?: number;
  start?: number;
  subject?: string;
};
type Link = { edge: FleetMapEdge; until: number };
const TERMINAL = new Set([
  'SUCCEEDED',
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'CANCELED',
  'STOPPED',
  'EXPIRED',
]);

/** Only navigable object URLs, with credentials and tracking/query data removed. */
export function workUrl(value: unknown): string {
  try {
    const url = new URL(text(value, 2000));
    if (!['https:', 'http:'].includes(url.protocol)) return '';
    url.username = '';
    url.password = '';
    url.search = '';
    url.hash = '';
    return url.href;
  } catch {
    return '';
  }
}

const referenceFromUrl = (url: string): string => {
  const match = /\/(?:pull|pulls|merge_requests)\/(\d+)(?:\/|$)/.exec(url);
  return match
    ? `${url.includes('/merge_requests/') ? '!' : '#'}${match[1]}`
    : '';
};

/** A bounded view of observed work and runs, never a reconstructed history. */
export class FleetStory {
  private works = new Map<string, Work>();
  private runs = new Map<string, Run>();
  private links = new Map<string, Link>();
  private seen = new Set<string>();

  private subject(raw: unknown, kindHint: string, now: number): string {
    const s = object(raw);
    const kind =
      s.kind === 'pull_request' || /pull_request|merge_request/.test(kindHint)
        ? 'pull_request'
        : s.kind === 'issue' || /issue/.test(kindHint)
          ? 'issue'
          : null;
    const url = workUrl(s.url);
    const ref = text(s.reference, 160);
    const repo = text(s.repo, 200);
    const key =
      text(s.key, 360) ||
      (repo && ref ? `${repo}${ref}` : '') ||
      url ||
      text(s.issue_id, 80);
    // A number without a repository/URL is ambiguous across trackers.
    if (!kind || !key) return '';
    const id = `work:${kind}:${key}`;
    // Prefer an existing identity for the same canonical URL or object key.
    const known = [...this.works].find(
      ([, w]) =>
        w.node.work_kind === kind &&
        ((url && w.node.href === url) || w.node.resource_id === key)
    );
    const identity = known?.[0] ?? id;
    const previous = this.works.get(identity);
    if (previous && previous.at > now) return identity;
    const label = `${kind === 'pull_request' ? 'PR ' : ''}${ref || text(s.key, 80) || (kind === 'issue' ? 'Issue' : 'PR')}${text(s.title, 160) ? `  ${text(s.title, 160)}` : ''}`;
    this.works.set(identity, {
      ...previous,
      at: now,
      node: {
        id: identity,
        type: 'work',
        label,
        status: 'Open',
        owner_ids: [],
        provider_ids: [],
        gaps: [],
        finding_count: 0,
        actions: [],
        ...previous?.node,
        resource_id: key,
        detail: repo || previous?.node.detail || null,
        href: url || previous?.node.href || '',
        ...(text(s.title) || !previous ? { label } : {}),
        work_kind: kind,
        last_seen_at: new Date(now).toISOString(),
      },
    });
    return identity;
  }

  private link(source: string, target: string, now: number): void {
    if (!source || !target || source === target) return;
    const id = `story:${source}>${target}`;
    this.links.set(id, {
      until: now + 6000,
      edge: {
        id,
        source,
        target,
        kind: 'caused',
        provenance: 'observed',
        impact_source: source,
        impact_target: target,
        gaps: [],
      },
    });
  }

  record(topic: string, wire: unknown, now = Date.now()): FleetMessage[] {
    const r = object(wire),
      p = object(r.payload);
    const type = text(r.type);
    if (!(
      (topic === 'flow_executions' &&
        ['execution_started', 'status_update'].includes(type)) ||
      (topic === 'code_activity' && type === 'code_activity')
    ))
      return [];
    const stableId = text(p.event_id || r.event_id || p.correlation_id);
    const atText = text(p.timestamp || r.timestamp);
    const parsed = atText ? parseUTCDate(atText).getTime() : now;
    const at = Number.isFinite(parsed) ? Math.min(now, parsed) : now;
    const dedup =
      stableId ||
      (topic === 'flow_executions'
        ? `${text(r.execution_id)}:${text(p.status)}:${atText}`
        : '');
    if (dedup && this.seen.has(dedup)) return [];
    if (dedup) this.seen.add(dedup);
    let result: FleetMessage[] = [];
    if (topic === 'flow_executions') {
      const flowId = text(r.flow_id || p.flow_id),
        execution = text(r.execution_id || p.execution_id);
      const context = object(p.fleet_context);
      if (!flowId || !execution || !text(p.status)) return [];
      const flow = `flow:${flowId}`;
      const previous = this.runs.get(execution);
      // A delayed RUNNING update cannot resurrect a completed execution.
      if (
        previous &&
        (at < previous.at ||
          (previous.end && !TERMINAL.has(text(p.status).toUpperCase())))
      )
        return [];
      const status = text(p.status).toUpperCase();
      const name =
        text(context.flow_name || p.flow_name, 160) || previous?.name || 'Flow';
      const startText = text(p.start_time);
      const start = startText
        ? parseUTCDate(startText).getTime()
        : (previous?.start ?? at);
      this.runs.set(execution, {
        flow,
        name,
        status,
        at,
        start,
        ...(TERMINAL.has(status) ? { end: previous?.end ?? at } : {}),
      });
      const triggerKind = text(context.trigger_kind || p.trigger_kind);
      let subject =
        this.subject(context.subject ?? p.trigger_ref, triggerKind, at) ||
        previous?.subject ||
        '';
      const resumeUrl = workUrl(context.resume_pr_url);
      if (resumeUrl)
        subject = this.subject(
          {
            kind: 'pull_request',
            url: resumeUrl,
            reference: referenceFromUrl(resumeUrl),
          },
          'pull_request',
          at
        );
      this.runs.get(execution)!.subject = subject;
      this.link(subject, flow, now);
      const parent = this.runs.get(
        text(
          context.parent_execution_id ||
            context.resume_from_execution_id ||
            context.retry_of_execution_id
        )
      );
      if (parent) this.link(parent.flow, flow, now);
      const node = this.works.get(subject)?.node;
      const elapsed = Number.isFinite(start)
        ? Math.max(0, Math.round((at - start) / 1000))
        : 0;
      const duration =
        elapsed >= 60
          ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
          : `${elapsed}s`;
      const state = TERMINAL.has(status)
        ? status === 'COMPLETED'
          ? 'succeeded'
          : status.toLowerCase()
        : previous
          ? 'running'
          : 'started';
      const content = TERMINAL.has(status) ? duration : (node?.label ?? '');
      result = [
        {
          from: flow,
          to: subject || flow,
          actor: flow,
          kind: 'code',
          icon: 'diagram-2',
          label: `${name} ${state}${content ? `  ${content}` : ''}`,
        },
      ];
      // The controlled publication path already emits the bound PR URL.
      // Select only that reference, never the result body or output summary.
      const output = object(p.result);
      const prUrl = workUrl(output.pr_url);
      if (prUrl && TERMINAL.has(status)) {
        const prId = this.subject(
          {
            kind: 'pull_request',
            url: prUrl,
            reference: referenceFromUrl(prUrl),
          },
          'pull_request',
          at
        );
        const pr = this.works.get(prId)!;
        if (node?.work_kind === 'issue') {
          pr.node.parent_id = subject;
          node.status = 'PR open';
          this.link(subject, prId, now);
        }
        this.link(flow, prId, now);
        result.push({
          from: flow,
          to: prId,
          actor: flow,
          kind: 'code',
          icon: 'journal-code',
          label: `Published ${pr.node.label}`,
        });
      }
      const reported = (
        {
          approve: 'approved',
          request_changes: 'changes requested',
          comment: 'commented',
        } as Record<string, string>
      )[text(output.review_action)];
      if (output.review_posted === true && reported && TERMINAL.has(status)) {
        const reviewTarget = this.works.get(subject);
        if (reviewTarget?.node.work_kind === 'pull_request') {
          // This is the agent's structured report, not a forge confirmation.
          if (
            !reviewTarget.node.review_state ||
            reviewTarget.node.review_state.startsWith('Reported')
          )
            reviewTarget.node.review_state = `Reported ${reported}`;
          this.link(flow, subject, now);
          result.push({
            from: flow,
            to: subject,
            actor: flow,
            kind: 'code',
            icon: 'eye',
            label: `Reported review ${referenceFromUrl(reviewTarget.node.href) || reviewTarget.node.label}  ${reported}`,
          });
        }
      }
    } else {
      const action = text(p.action);
      const allowed = [
        'issue_opened',
        'issue_updated',
        'issue_labeled',
        'issue_unlabeled',
        'pull_request_updated',
        'opened',
        'reviewed',
        'commented',
        'merged',
      ];
      if (!allowed.includes(action)) return [];
      const raw = object(p.subject);
      const fallback =
        !Object.keys(raw).length && (text(p.pr) || workUrl(p.url))
          ? {
              kind: 'pull_request',
              key: text(p.pr) || workUrl(p.url),
              reference: text(p.pr).match(/[#!]\d+$/)?.[0],
              title: p.title,
              url: p.url,
            }
          : raw;
      const subject = this.subject(
        fallback,
        action.startsWith('issue_') ? 'issue' : 'pull_request',
        at
      );
      const issue = this.subject(p.related_issue, 'issue', at);
      const work = this.works.get(subject);
      if (work && work.at > at) return [];

      const flow = text(p.flow_id || r.flow_id),
        agent = text(p.managed_agent_id),
        user = text(p.actor_user_id);
      const actor = user
        ? `user:${user}`
        : flow
          ? `flow:${flow}`
          : agent
            ? `agent:${agent}`
            : '';
      if (work) {
        if (issue && issue !== subject) {
          work.node.parent_id = issue;
          const parent = this.works.get(issue);
          if (parent)
            parent.node.status = action === 'merged' ? 'Merged' : 'PR open';
          this.link(issue, subject, now);
        }
        const review = text(p.review_state);
        if (
          ['commented', 'changes_requested', 'approved', 'dismissed'].includes(
            review
          )
        )
          work.node.review_state = review;
        if (action === 'merged') {
          work.node.status = 'Merged';
          work.expires = at + 120_000;
          const parent = this.works.get(work.node.parent_id ?? '');
          if (parent) {
            parent.expires = work.expires;
            parent.node.status = 'Merged';
          }
        } else if (action === 'issue_labeled' || action === 'issue_updated')
          work.node.status = 'Updated';
        this.link(actor, subject, now);
        // Merge contributors are association evidence, never a human merger.
        if (Array.isArray(p.flow_ids))
          for (const id of p.flow_ids.slice(0, 5))
            if (text(id)) this.link(`flow:${text(id)}`, subject, now);
      }
      const verb: Record<string, string> = {
        issue_opened: 'Opened',
        issue_updated: 'Updated',
        issue_labeled: 'Labelled',
        issue_unlabeled: 'Unlabelled',
        pull_request_updated: 'Updated',
        opened: 'Opened',
        reviewed: 'Reviewed',
        commented: 'Commented on',
        merged: 'Merged',
      };
      const ref =
        text(object(fallback).reference) ||
        text(p.pr) ||
        (action.startsWith('issue_') ? 'issue' : 'PR');
      const labels = Array.isArray(p.labels_added)
        ? p.labels_added
            .slice(0, 12)
            .map((x) => text(x, 80))
            .filter(Boolean)
            .join(', ')
        : '';
      const detail =
        action === 'reviewed'
          ? text(work?.node.review_state).replace(/_/g, ' ')
          : labels || text(object(fallback).title, 160);
      const label = `${verb[action]} ${ref}${detail ? `  ${detail}` : ''}`;
      if (actor || subject)
        result = [
          {
            from: actor || subject,
            to: subject || actor,
            actor: actor || subject,
            kind: 'code',
            icon: action === 'merged' ? 'check2-circle' : 'journal-code',
            label,
          },
        ];
    }
    this.trim();
    return result;
  }

  private trim(): void {
    while (this.works.size > 200)
      this.works.delete(this.works.keys().next().value!);
    while (this.runs.size > 400)
      this.runs.delete(this.runs.keys().next().value!);
    while (this.links.size > 800)
      this.links.delete(this.links.keys().next().value!);
    while (this.seen.size > 4000)
      this.seen.delete(this.seen.values().next().value!);
  }

  graph(
    base: FleetMapGraph,
    minutes: number | null,
    now = Date.now()
  ): FleetMapGraph {
    const since = minutes === null ? 0 : now - minutes * 60_000;
    const nodes = new Map(base.nodes.map((n) => [n.id, n]));
    for (const [id, w] of this.works) {
      if (w.at < since || (w.expires && now >= w.expires)) continue;
      nodes.set(id, w.node);
    }
    const flows = new Map<string, Run[]>();
    for (const run of this.runs.values()) {
      if (run.at < since || (run.end && now - run.end >= 60_000)) continue;
      flows.set(run.flow, [...(flows.get(run.flow) ?? []), run]);
    }
    // Live-run flows fade after their last run, including code-discovered flows.
    const knownFlows = new Set([...this.runs.values()].map((r) => r.flow));
    for (const id of knownFlows) if (!flows.has(id)) nodes.delete(id);
    for (const [id, runs] of flows) {
      const active = runs.filter((r) => !r.end).length;
      const latest = runs.reduce((a, b) => (a.at > b.at ? a : b));
      const status =
        active > 1 ? `${active} running` : active ? 'RUNNING' : latest.status;
      nodes.set(id, {
        id,
        type: 'flow',
        resource_id: id.slice(5),
        label: latest.name,
        href: `/console/flows/${encodeURIComponent(id.slice(5))}`,
        detail: 'Flow',
        owner_ids: [],
        provider_ids: [],
        gaps: [],
        finding_count: 0,
        actions: [],
        ...nodes.get(id),
        ...(nodes.has(id) ? {} : { label: latest.name }),
        status,
        last_seen_at: new Date(latest.at).toISOString(),
      });
    }
    const edges = [
      ...base.edges,
      ...[...this.links.values()].map((l) => l.edge),
    ].filter((e) => nodes.has(e.source) && nodes.has(e.target));
    return { ...base, nodes: [...nodes.values()], edges };
  }

  flowForExecution(id: string): string | undefined {
    return this.runs.get(id)?.flow.slice(5);
  }

  activeEdges(now = Date.now()): Set<string> {
    return new Set(
      [...this.links].filter(([, l]) => l.until > now).map(([id]) => id)
    );
  }

  /** Used to expire runs and work without running an animation loop forever. */
  hasLiveActivity(minutes: number | null, now = Date.now()): boolean {
    const since = minutes === null ? 0 : now - minutes * 60_000;
    return (
      [...this.works.values()].some(
        (work) =>
          work.at >= since && (work.expires === undefined || now < work.expires)
      ) ||
      [...this.runs.values()].some(
        (run) =>
          run.at >= since && (run.end === undefined || now - run.end < 60_000)
      )
    );
  }
  clear(): void {
    this.works.clear();
    this.runs.clear();
    this.links.clear();
    this.seen.clear();
  }
}
