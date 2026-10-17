# Weekly report settings

Account settings expose three independent switches through
`GET` and `PUT /api/v1/weekly-report/preferences`:

- `account_enabled`: weekly reports for owners and admins, on by default.
- `members_enabled`: personal reports for regular members. The stored nullable
  field uses the edition's plan default until an admin explicitly changes it.
- `member_policy_findings_enabled`: allow personal reports to include explicitly
  attributed stored governance findings, off by default.

`user_enabled` remains the recipient's personal unsubscribe preference.
Account switches require the primary owner, a superuser, or `manage_account`;
a member may update only their own unsubscribe preference. CRUD serializes
account changes and commits their audit records atomically. The console controls
are gated by the existing governance-review capability. Report delivery, plan
defaults and comparison orchestration are provided by the commercial plugin.

The read-only report projections filter by account and optional attributed user
before aggregation. Personal approvals count requests triggered through that
user's keys or managed agents and requests on which they recorded a vote. Raw
finding evidence is excluded from personal projections. Adjacent report windows
are half-open, and replay-validation traffic is excluded from usage totals.

## Most valuable work: recorded outcomes

The weekly review can show sessions and flow executions ranked by recorded
repository outcomes per estimated dollar. This section appears when the server
advertises `weekly_work_value` and the reader has View cost permission. The digest
shows five entries; **View all recorded outcomes** opens the console for the same
reporting window. Changing the Week selector switches the outcomes window too.

Each entry states its PRs/MRs, changed lines, linked issues closed or referenced,
review comments addressed, and estimated cost. The method explains the score and
shared credit. Unknown or partially priced work is **Unranked**, rather than
counted as free. This measures observed repository activity, not business value.
Review resolution, a later edit of the commented line, or an accepting author
reply establishes addressed evidence; missing evidence stays unaddressed.

The console reads cached forge evidence. The feature makes no model calls and
incurs no LLM spend. Forge observations refresh after seven days when available;
failed refreshes can retain older observations. Missing attribution or incomplete
forge coverage can leave work out of the ranking.
