# Fleet Map

The Fleet Map connects people, devices, agents, models and providers. Open it from
Device fleet or the Overview inventory card. Device evidence depends on the
configured fleet collector; missing evidence is identified on the map.

Search highlights matching nodes. Show selects resource kinds, with Accounts hidden
by default. State includes onboarded, discovered and inactive agents. Owner,
Provider and Window narrow the loaded view. Gaps appears only when the view contains
a governance gap. Select a node for dependencies, blast radius and resource links.

Instance administrators can choose Columns or Spatial. The spatial layout packs
machines and their agents beside the model/provider core. Drag a machine to move its
contents. Your first drag saves the whole visible arrangement for your user and map
scope. Refreshes and membership changes preserve that arrangement and camera; new
resources join beside their links. Inactive positions are retained for seven days.
More > Reset layout repacks the map and offers Undo for six seconds. Fit changes the
camera without moving your nodes.

Device fleet and full screen pan on plain wheel. In the Overview card, plain wheel
scrolls the page; drag empty canvas to pan. Ctrl/Cmd+wheel zooms around the pointer.
The bottom-right controls provide zoom, 100%, Fit and full screen. A crowded spatial
map keeps a minimum readable scale and allows panning to overflow.

## Live metadata

Bubbles in More shows up to four short notes for four seconds. A real model start
shows thinking; completion shows available input/output tokens. Tool notes show
names, servers and outcomes. Native allowed means permission granted, not proof that
the tool finished. Tool badges and the selected node's Recent section count events
received since opening the map, within the selected window. They are bounded client
samples, not historical totals.

The map selects metadata, never model prompts or replies. Approval command text
comes from the existing display-redacted approval payload, with extra credential
scrubbing. Generic command/path capture is not enabled by this UI.

## Observed work

Flow start/status metadata creates a work reference when it identifies an issue or
PR. Concurrent executions share one flow node. A completed flow leaves after 60
seconds. Controlled publication's recorded PR URL attaches the PR to its trigger
issue. Structured reviewer results are labelled **Reported approved** or
**Reported changes requested**. A confirmed merge takes precedence over that prior
review state; merged work remains for two minutes.

The map highlights observed associations for six seconds, then keeps their
hairlines while both endpoints remain. Actual parent and repair-resume IDs can
connect runs. A shared issue or PR does not establish a unique causal predecessor.
Contributing flow IDs on a merge do not identify the person who merged it.

Richer tracker titles, label changes, confirmed review verdicts and verified human
actors require normalized publisher metadata. Missing fields remain unknown. The
live view does not backfill a complete issue/review history after refresh.
