---
status: non-normative
---

# PRD: portfolio review flow for a repository full of independent projects

> **Status: findings / design note. Not shipped behaviour.** This page records observations or a proposed design. Nothing here is a product capability unless a linked release note says so.

Editions: OSS, Cloud, Enterprise. Unless stated otherwise, everything on this page ships in OSS.

Tracking issue: #623. Sibling PRDs: #621 (flow to flow delegation) and #622
(agent authored operator notes). The shipped behaviour is documented in
[Portfolio review](../guide/flows/portfolio-review.md); this page is the
product requirement it was built against, kept so the scope decisions and
the remaining open questions live in the repository rather than only in the
tracker.

## Problem

A consultancy, agency, managed service provider or platform team keeps many
independently built projects in one repository. Reviewing them is a per
project manual effort that nobody funds until something breaks. The
recurring question is "which of these projects is a liability", and
answering it today costs a week of reading.

Preloop already reviews one repository well. The
[full-repo review presets](../guide/flows/repo-review-presets.md) share one
skeleton:

| preset | lens | result schema |
| --- | --- | --- |
| `backend/presets/008-architecture-strategy-review.yaml` | architecture and strategy | family schema |
| `backend/presets/009-repo-code-health-review.yaml` | code health | `preloop.review.codehealth/v1` |
| `backend/presets/006-release-security-audit.yaml` | release security | `preloop.cra.releaseaudit/v1` |

What is missing is the layer above: discover the projects, ask a human which
ones are worth money, run the existing lenses once per project, and turn the
results into a portfolio report plus a ranked list of tracked follow ups.

`009` already accepts `target_repo_path`, `depth`, `max_files_opened`,
`focus_paths` and `exclude_paths` from the trigger payload, so the code
health lens needs no new preset. The preset loader merges preset directories
by slug with later directory override (`backend/preloop/flow_presets.py`).

## Users

- Consultancies and agencies keeping customer projects in one repository.
- Internal platform teams who inherited a repository whose original authors
  have left.
- Acquirers doing technical due diligence before signing.

The first named external design partner is a consultancy maintaining legacy
customer projects in one repository. Nothing in this PRD assumes that
partner's stack or project count.

## Goals

1. Answer "which of these projects is a liability" for one repository in one
   run, with a cost ceiling a human sets before any money is spent.
2. Reuse the existing review lenses unchanged in their judgement; add only
   the scoping they need to look at one project inside a repository.
3. Make coverage checkable: every project the run did not review is named,
   with the reason (project cap, child cap, cost ceiling, no SBOM).
4. Produce two durable outputs: a portfolio report and a ranked list of
   follow ups a human approved, each of which can become one tracker issue.

## Non-goals

- **Modernisation.** No fix, upgrade, refactor or rewrite. Each accepted
  follow up becomes one issue for
  `backend/presets/011-automated-issue-implementation.yaml`, one at a time,
  with a human in between. There is no "modernise everything" run.
- **Cross-repository review.** One repository per run, like every other
  preset in the family.
- **A new reviewer.** The orchestrator never reads project source code and
  never forms an opinion about it. Its job is discovery, delegation and
  aggregation. The moment it reads code, the cost model stops being per
  child and the coverage claim stops being checkable.
- **SBOM generation.** The security presets verify an SBOM a build produced
  (`004-sbom-verify.yaml`, `005-sbom-exploit-check.yaml`); this flow does not
  generate one either.

## Scope

### The shape of one run

1. **Discovery** (orchestrator, deterministic). A project is a directory with
   at least one manifest or build descriptor from a closed detector list.
   Named exclusions, a depth cap and a nesting rule keep the list
   reproducible; truncated branches are recorded, not dropped.
2. **Triage hint** (orchestrator, deterministic). A points table over the
   discovery facts (staleness, missing README, CI, tests, licence, end of
   life runtime from a payload supplied table). It is a fact sheet, not a
   quality score.
3. **Selection question.** One batched `ask_user` call: rows per project, a
   multi-select of project paths, depth and a run cost ceiling. Parks the
   run. Expiry defaults to inventory only.
4. **Fan out.** One child execution per selected project per lens, through
   `run_flow`, in rank order, so a run stopped by a ceiling has done the
   worthwhile work first.
5. **Aggregation.** One row per discovered project, one lens row per lens,
   read from the children's own result envelopes.
6. **Follow ups question.** One batched `ask_user` call over candidate follow
   ups, each backed by a lens finding. Expiry defaults to keep nothing.
7. **Publication and filing** (platform, after the agent exits). The report
   lands as a pull request on a stable branch; approved follow ups become
   tracker issues keyed on the follow up id. The agent itself has no write
   tool.

### How it uses flow to flow delegation (#621)

The fan out is a direct consumer of the delegation object shapes in
[Delegation object shapes](../guide/flow-delegation-shapes.md):

| delegation record | what the portfolio flow puts in it |
| --- | --- |
| request (A2A `Message`) | data part `{"target_repo_path", "project_path", "depth"}` and nothing else: no model, harness or instruction overrides |
| child (A2A `Task`) | one per `(project, lens)`, labelled `<project path>\|<lens slug>`; `preloop.ai/cost` feeds the run ceiling |
| result (artifact with a data part) | the lens's own result envelope, aggregated verbatim |

Rules the flow depends on from the delegation epic:

- **Callable list.** The orchestrator may start only the lens flows named on
  its callable list, each with `max_children` and `max_usd_per_child`. A call
  outside the list comes back `TASK_STATE_REJECTED` with
  `flow_not_callable`; the flow records it in `fan_out.lenses_refused` and
  continues.
- **Depth.** Lens flows have no `run_flow` tool, so children are depth 1 and
  cannot delegate again.
- **Ceilings.** Direct children per execution (25), per tree USD, and the
  parked child wait are instance ceilings; a cap hit is a coverage
  statement, never a run failure.
- **Parking.** `wait: true` on the last call only parks the parent on
  `WAITING_FOR_CHILDREN` with no container and no budget, and resumes it
  once with every completion record.
- **Child approvals.** A lens that asks a question surfaces it as described in
  [Approvals in child executions](../guide/flow-delegation-child-approvals.md).

### How it relates to operator notes (#622)

Operator notes are not on the critical path. The orchestrator does not write
notes in v1: its outputs are the result envelope, the evidence pack, the
report pull request and filed issues. A later version may leave a note for
the implementation flow on each filed follow up; that is an open question
below, not scope.

### The lenses

| lens slug | preset | per child ceiling | runs when |
| --- | --- | --- | --- |
| `docs-currency-review` | `016-docs-currency-review.yaml` (new, fourth lens) | 2.0 USD | always |
| `repo-code-health-review` | `009-repo-code-health-review.yaml` | 3.0 USD | always |
| `release-security-audit` | `006-release-security-audit.yaml`, scoped to one project | 3.0 USD | only where the project has its own SBOM |

Where no SBOM exists the security row is `lens_status: not_checkable` with
the reason `no SBOM available`. The name is `not_checkable`, matching the
review family's rule that `not_checkable` is required rather than assumed;
an earlier draft called it `skipped`. A `not_checkable` row is never a pass.

### Report contract

- `/workspace/result.json` with schema `preloop.review.portfolio/v1`.
- An evidence pack under `/workspace/evidence/` opening on the family's
  one-minute verdict cover.
- `coverage.not_reviewed` names every unreached project with its reason;
  `coverage.plan_completed` is false when any cap was hit.
- The agent always writes `filed: false`; only the platform filing step
  rewrites it.

## Phased plan

1. **Phase 0, no platform change.** The docs currency preset plus the
   orchestrator running inline for a small project count. Exercises both
   `ask_user` forms, park and resume, the expiry defaults and the report
   contract.
2. **Phase 1.** Fan out through `run_flow` once delegation lands, one child
   per project per lens, with declared coverage when a cap is hit.
3. **Phase 2.** The security lens scoped to a project, the `not_checkable`
   result when no SBOM exists, and the decision on scanner binaries in the
   agent image.
4. **Phase 3.** Scheduled re-runs with an automatically seeded baseline, and
   the portfolio report landing as a pull request rather than a direct
   commit.

## Acceptance

The PRD is satisfied when, on a local stack:

1. A run over a repository with more projects than the child cap completes
   successfully, names every unreached project in `coverage.not_reviewed`,
   and sets `coverage.plan_completed` to false.
2. Letting the selection question expire yields an inventory only report
   whose verdict is not `pass`; letting the follow ups question expire
   leaves every candidate unapproved and files nothing.
3. A project without an SBOM shows a `not_checkable` security row with its
   reason and starts no security child.
4. The orchestrator's tool allowlist contains no write tool; the report pull
   request and the filed issues are produced by platform steps after exit.
5. A re-run with an unchanged report makes no commit and no provider call.
6. A human reads the dogfood report (#647) and agrees with every project
   status; a false healthy on a project with no tests and no CI is a stop.

## Open questions

1. **Security lens in v1.** Ship the `not_checkable` row (recommended, cheap)
   or fund scanner binaries before the first external partner run. The
   [scanner binaries decision note](../guide/flows/scanner-binaries-decision.md)
   recommends a derived, digest-pinned image with Trivy registered as an
   environment profile rather than a change to the default image. Still open:
   who owns refreshing the baked advisory database.
2. **Who opens the report pull request.** Recommended and adopted: the
   orchestrator stays read only and a platform step publishes, because the
   alternative gives write rights to a flow that reads many untrusted
   projects. Open: whether the same step should ever publish to a repository
   other than the one reviewed.
3. **Default fan out and USD ceilings.** Defaults today are 5 projects (hard
   cap 12) and 20 children (hard cap 25), with no run USD ceiling unless the
   human sets one. Open: whether a run without a human set ceiling should
   get a default one, and what number, once #647 has measured cost per
   child (not yet measured: the dogfood instance had no model pricing).
4. **Re-run and the open report pull request.** Recommended and adopted:
   update the open one, keyed on a stable branch per portfolio document.
   Open: whether a scheduled re-run should comment on the pull request with
   the delta against the seeded baseline.
5. **Operator notes on follow ups.** Should each filed follow up carry an
   operator note (#622) for the implementation flow, or is the issue body
   enough?
6. **Selection at scale.** The selection form shows the 150 highest ranked
   rows. Is that the right cut for estates of several hundred projects, or
   do we need filtering by stack or path prefix on the form?
7. **Partial child failure.** When a lens child fails rather than being
   refused, should the follow ups question still be asked for the projects
   that did complete? Today the report is finished and the failed row is
   named; whether that is enough for a paying partner is untested.

## Proposed child issues

These were filed under #623 and are listed here so the PRD and the tracker
agree. State as of 2026-10-11.

| issue | title | phase | state |
| --- | --- | --- | --- |
| #641 | add a docs currency review preset as the fourth review lens | 0 | closed |
| #645 | add the portfolio review orchestrator running inline | 0 | closed |
| #646 | fan the portfolio review out to child executions per project | 1 | closed |
| #690 | put the run ceiling and the report toggle on the portfolio question forms | 1 | closed |
| #642 | scope the release security audit to one project inside a repository | 2 | closed |
| #643 | decide whether to ship scanner binaries in the agent image | 2 | closed |
| #644 | seed a review baseline from a previous execution id | 3 | closed |
| #648 | land the portfolio report through the existing pull request path | 3 | closed |
| #687 | file the approved portfolio follow ups as tracker issues | 3 | closed |
| #647 | dogfood the portfolio review and record cost and defects | gate | open |

Proposed follow ups from the open questions above (not filed):

- **Default run ceiling.** Once #647 records cost per child, propose a
  default `max_cost_usd` for runs where the human leaves it empty.
- **Scanner image owner.** Implement the derived scanner image from the
  decision note and name an owner for advisory database refresh.
- **Baseline delta on re-run.** On a scheduled re-run, post the delta
  against the seeded baseline on the open report pull request.
- **Follow up notes.** Attach an operator note to each filed follow up for
  the implementation flow, if open question 5 resolves that way.
- **Selection filters.** Add stack and path prefix filters to the selection
  form for estates past 150 projects.
- **Partial failure policy.** Define and test the follow ups question when
  one or more lens children fail.

## References

Internal spec memo `product/memos/2026-09-15-portfolio-review-and-flow-delegation.md`
and its dogfood plan (private, quoted rather than linked). Scope discipline
quoted from the memo: "v1 is inventory, assessment and ranked follow up
issues. Modernisation is executed by the issue implementation preset, one
issue at a time."
