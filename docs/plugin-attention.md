# Plugin attention sources

An enabled plugin can implement `Plugin.get_attention_sources()` and return
`AttentionSource(id, permission, provider)` objects. The async provider receives
`(db, user)` and returns `list[ExtensionAttentionItem]`. Read through core CRUD and
scope every read to `user.account_id`. A provider may offload synchronous CRUD
using `run_db_off_loop`. The endpoint checks the declared permission before invoking
the provider; account owners and superusers also qualify. Restricted credentials
cannot read attention items.

`GET /api/v1/attention/extension-items` merges authorized contributions, isolates
provider failures and assigns each source a `plugin-name:source-id` namespace.
Without providers it returns `[]`. Item fields are `id`, `source`, `title`, `detail`,
`href`, `severity` (`action` or `info`) and timezone-aware `opened_at`. Destinations
must be local `/console` paths. Providers own wording, policy and finding lifecycle.
The core owns presentation only. Action items use the existing warning treatment;
information uses neutral low severity. Both use existing account dismissals, with
a fingerprint of the incident time and displayed content so a new incident returns.

Optional evidence processors can use the inert `ObservationVerification` model and
`crud_observation_verification` store. A trusted server verifier success binds one
source and subject digest to policy/evidence digests and an expiring time window.
There is no public or collector write endpoint, and core runs no verifier. Optional
processors own interpretation and invalidation. Missing records remain unverified.
Finding changes and verification changes retain evidence in the existing audit
store, subject to the deployment's audit retention policy. Repeated identical
finding evaluations do not append duplicate history.
