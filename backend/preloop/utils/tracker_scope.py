"""Project identity aliases shared by discovery and API scope filtering."""


def project_scope_identifiers(
    tracker_type: str, identifier: str, *, jira_key: str | None = None
) -> set[str]:
    """Return stable identity and the supported legacy Jira project key."""
    identifiers = {str(identifier)}
    if tracker_type == "jira" and jira_key:
        identifiers.add(str(jira_key))
    return identifiers
