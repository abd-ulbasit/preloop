# Weekly governance review

Editions: Cloud, Enterprise. The schedule and the storage tables ship in OSS, but without the Enterprise plugin both scheduled tasks do nothing.

The weekly governance review checks each week's agent sessions against a policy document that your account owns. It reports what it finds and never blocks work. This page is for operators. It covers what the review reads and sends, what it costs, how to choose the analysis model, and how to edit the policy.

!!! note "What ships today"
    Stage A (session summaries), the policy document, the review settings and the budget cut-off are available now. Stage B (the weekly pass that writes findings), the governance section of the Monday digest and the console page that lists findings arrive in later 0.18.0 changes. This page marks each part that has not shipped yet.

## Privacy

With the platform model, scrubbed session excerpts and digests are sent to the platform's model provider. Full transcripts are not sent.

In detail, a session summary request contains:

- Counts measured from gateway usage rows: models, requests, tokens, tools attached and used, failures, retries, cache behaviour.
- The compiled watchlist from your active policy document.
- The previous rolling summary of the same session, if one exists.
- A sample of turns: the first two, the last two and up to four of the longest user turns. Each sampled turn is cut to 1,500 characters.

The sample is scrubbed for secrets before it is cut, so a cut cannot expose part of a credential. A request is limited to 6,000 input tokens. Middle samples are dropped first when it would be larger. The model returns intent, outcome, notable points, triviality, task class and up to 10 policy observations, each with a quote of at most 280 characters. The model cannot overwrite measured counts.

What is never sent: full transcripts, unscrubbed text, and any turn longer than 1,500 characters in full.

When an account uses its own analysis model, the same content goes to that model's provider through your gateway, under your provider agreement. On a self-hosted install there is no platform model; text goes only to the model the operator configures.

To stop all analysis, set the mode to `off` (see [Settings](#settings-and-permissions)). Stored summaries are kept until the account is deleted or retention removes them.

## How it works

The review runs in two stages.

**Stage A: session summaries, hourly.** A sweep runs at minute 0 of every hour (UTC). It summarises a segment of a session when the session has closed, has been idle for 60 minutes, has about 50,000 new transcript tokens, or reaches the weekly cut-off on Monday at 07:00 UTC with new content. A session that lives for weeks gets one segment per trigger, so each week reads only new content. The sweep handles at most 200 sessions per account per run. Measured counts are always stored. The model call adds the summary and the policy observations.

**Stage B: weekly review, Monday 07:00 UTC (not shipped yet).** Stage B reads the week's summaries and the deterministic signals, never raw transcripts, and writes findings. The Monday 09:00 UTC digest will then show them in a governance section.

The first review runs on the first scheduled Monday after the review is enabled, not when it is enabled.

## What the report contains (not shipped yet)

This describes the governance section planned for the Monday digest. It will appear with Stage B.

- **Findings**, each with evidence excerpts (at most 280 characters, scrubbed), links to the sessions and audit events, and one concrete suggestion. A finding is one of:
    - a **violation**: a `must` rule missed,
    - an **inefficiency**: a `prefer` rule missed. The suggestion quotes the rule.
    - an **anomaly**: spend outliers, first-seen agents, tools or clients, error-rate jumps.
- **A coverage line** at the top: "Reviewed n of m sessions (k segments) for the week of ...; summaries started ...".
- **Partial data.** Summaries are missing in the first week, and some may be missing in later weeks after a budget stop or cut-off. The report still renders:
    - Sections built from gateway metadata do not need summaries and always render. These include spend by developer and model, outliers, approvals, block and approval policy hits, first-seen agents, tools and clients, error rate, p95 latency and cache share. Suggestions derived from them, such as unused attached tools, cache misses and retries, also appear.
    - When no session has been reviewed, the section says that summaries are still accumulating and the next report will include them. It shows counts as "not yet reviewed", never as 0, and shows no empty findings table.
    - A run stopped by its budget is labelled partial and keeps the findings it has.

Recipients see violations and anomalies with `view_policies` and inefficiencies with `view_cost`.

## The policy document

Each account has one active policy document in Markdown. The first time the document is read, the shipped default template is stored as version 1. Every edit or restore creates a new version, and history is never deleted. Each version is compiled into a watchlist that Stage A sends to the model.

The template has two rule classes:

- **`must` rules** are hard rules. A miss is a violation. Defaults: approved agents are the agents onboarded to the account; approved models are the models the account allows through the gateway; no secrets or customer personal data in prompts; a list of forbidden technologies for you to fill in.
- **`prefer` rules** are soft rules. A miss is an inefficiency, and the suggestion quotes the rule. Defaults:

| Rule | Default |
|---|---|
| `prefer.model_per_task` | Triage and summaries on a small model |
| `prefer.session_cost` | $2 per session |
| `prefer.ticket_cost` | $10 per ticket |
| `prefer.retry_limit` | 5 repeats of the same error in a session, or the same error in 3 sessions |
| `prefer.caching` | Cache-read share of at least 0.2, and fewer than 3 prompt-prefix changes |
| `prefer.tool_hygiene` | Detach a tool or skill attached and unused for 5 sessions |
| `prefer.failure_rate` | 10 percent of requests |

Each rule has a heading and an HTML comment block that the compiler reads:

```markdown
<!-- governance-rule
ref: prefer.session_cost
class: prefer
kind: session_cost
signal: session_cost
usd: 2
check: A session stays within the cost expectation.
-->

### prefer.session_cost

Per-session cost expectation: $2.
```

To change a threshold, edit the value in the block (here `usd: 2`), not only the prose. Keep the block with its heading. A version whose blocks do not compile is rejected and not stored. Rules that apply only to your account go under "This account". Start each line with `must:` or `prefer:` so the review can tell a violation from an inefficiency.

The policy API, under `/api/v1/governance-review/`:

| Method and path | Permission | Purpose |
|---|---|---|
| `GET policy` | `view_policies` | Active version and its watchlist |
| `PUT policy` | `manage_policies` | Save a new version |
| `GET policy/versions` | `view_policies` | Version history, newest first, paged |
| `POST policy/versions/{version}/restore` | `manage_policies` | Copy an old version into a new one |
| `GET policy/watchlist` | `view_policies` | Compiled watchlist of the active version |

Every write is recorded in the audit log.

## Settings and permissions

`GET` and `PUT /api/v1/governance-review/settings` read and change the account's mode and analysis model. Reading needs `view_policies`. Changing needs `manage_policies`. The response includes the privacy sentence above.

| Mode | Meaning |
|---|---|
| `on_platform` | The review runs on the deployment's built-in model. |
| `on_own_model` | The review runs on a model the account owns and can call through the gateway. |
| `off_over_budget` | The budget rule turned the review off. |
| `off` | Turned off by a user. Both stages stop. |

An account with no stored settings uses the deployment default: `on_platform` on Preloop Cloud, and on a self-hosted install `off` until an analysis model is configured (see [Self-hosted](#self-hosted-analysis-model)).

## Cost and the allowance cut-off

The platform model is the deployment's built-in default model. Governance code does not name a model. Spend is recorded with the purpose `governance_session_digest` (Stage A) or `governance_review` (Stage B). A run counts both purposes in its week.

Limits that apply to all plans when the platform model is used:

- Stage A stops for the day at $0.05 of platform-funded spend per account. Sessions over the cap keep their measured counts without a summary.
- A run stops at $0.25 of platform-funded spend for its week, or as soon as the next call would exceed the remaining allowance or has no known price. The run is marked partial.

What happens after a run depends on the plan:

- **Free and trial plans.** A run that spends more than $0.10 of platform-funded analysis turns the review to `off_over_budget`. The notice reads: "This review used $X of Preloop-funded analysis, over the $0.10 free allowance, so it is now off. Keep it on with your own model in Settings > Models."
- **Paid plans.** The $0.10 rule does not apply. The review spends from the account's built-in model allowance. After a run, if the remaining allowance is below the larger of these two values, the review is turned off:
    - the projected cost of the next run plus a $0.05 safety margin,
    - 10 percent of the included allowance.

    First, the account's primary user and admins receive an email. It gives the reason, the allowance figures (included, spent, reserved, remaining, projected next review, threshold) and two ways to continue: top up the allowance, or set an own analysis model. If the email cannot be sent, the review stays on and the step is retried.

**Own model.** Once an own analysis model is set, the cut-off never applies. The review keeps running on that model, paid by the account, and only the account's normal gateway budgets apply. `POST /api/v1/governance-review/keep-on` (permission `manage_policies`) switches to `on_own_model`. Without a model id it uses the account's default gateway model, which must be a model the account owns (Settings > Models).

Every mode change is recorded in the audit log as `governance_review.mode_changed`.

Deployment operators can tune the limits with environment variables on the worker:

| Variable | Default |
|---|---|
| `GOVERNANCE_SESSION_DIGEST_DAILY_CAP_USD` | `0.05` |
| `GOVERNANCE_REVIEW_PLATFORM_HARD_CAP_USD` | `0.25` |
| `GOVERNANCE_REVIEW_PLATFORM_OFF_THRESHOLD_USD` | `0.10` |
| `GOVERNANCE_REVIEW_ALLOWANCE_SAFETY_MARGIN_USD` | `0.05` |
| `GOVERNANCE_REVIEW_ALLOWANCE_MIN_REMAINING_PERCENT` | `10` |

## Self-hosted analysis model

A self-hosted Enterprise install has no platform model and no platform funding. Set the analysis model to a gateway alias that your deployment serves:

| Helm value | Environment variable | Default |
|---|---|---|
| `governanceReview.enabled` | `GOVERNANCE_REVIEW_ENABLED` | unset (the application default is `true`) |
| `governanceReview.analysisModel` | `PRELOOP_GOVERNANCE_ANALYSIS_MODEL` | unset |

```yaml
governanceReview:
  analysisModel: example-flash
```

The chart sets both variables on the API, the workers and the scheduler, and only when the value is set. `governanceReview.enabled: false` removes the hourly sweep and the Monday review from the scheduler. When `analysisModel` is set, accounts with no stored settings default to `on_own_model` and call that alias through the gateway. An account can still choose its own model or turn the review off.

## Related

- [Cost analytics and budgeting](../architecture/cost.md): the Monday digest that will carry the governance section.
- [Subject-scoped governance](../architecture/governance.md): the review's tables and schedule.
- [Model content policies](model-content-policies.md): notify, block and approval rules whose matches the review reads.
