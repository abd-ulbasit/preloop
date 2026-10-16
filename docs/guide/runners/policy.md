# Account runner requirements

When the Enterprise runner policy provider is enabled, administrators can use
**Settings → Account → Personal runner policy** to set an account default and
role or team overrides. Requirements are optional, required, or forbidden;
required policies can mandate flow execution, harness inventory, and remote
sessions. Grace periods allow members time to connect a runner. Enforcement
can report compliance or warn the member and include it in the weekly digest.
Saving requires permission to manage policies; readers can inspect without editing.

**Runners → Member runner compliance** shows each member's compliance state,
last heartbeat, runner status, and reported harness governance. Inventory can be
stale or unknown independently of heartbeat compliance. The roster requires
permission to view users and paginates the account's members. A required member
with a warning sees installation or reconnection guidance throughout the console.

Team overrides win over role overrides, then the account default. Multiple
matching entries within a scope use required before forbidden before optional.
A forbidden policy suppresses the interactive login install offer; it does not
uninstall an existing runner. These policies do not stop work on unenrolled
machines or block flow routing. Device-wide installation remains an MDM concern.

Install a runner using the [Linux](quickstart-linux.md),
[macOS](quickstart-macos.md), or [Windows](quickstart-windows.md) quickstart.
