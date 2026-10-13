"""Per-flow approval policy for unattended persistent runs (#1671).

A scheduled persistent flow runs on an agent host with nobody at the
terminal. Its native tool calls still go through the Preloop permission
hook, so a call that escalates to a human waits until the approval expires
and the turn fails. ``agent_config.approval_policy`` lets the flow owner
auto-approve a narrow set of tool calls for that one execution:

.. code-block:: json

    {"mode": "auto_allow",
     "tools": ["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
     "paths": ["<workspace>"]}

The grant is held server side. It applies only while the execution is
active, only to calls from the managed agent the execution was dispatched
to, and only to the one native session that first presents the execution
id (the sidecar sets ``PRELOOP_FLOW_EXECUTION_ID`` for the session it
starts). Calls outside the policy, such as paths outside the listed
directories, network commands or ``git push``, take the normal approval
path. Every auto-approved call is still recorded as an approval request
resolved with ``auto_approved_reason = "flow_approval_policy"``.
"""

from __future__ import annotations

import logging
import posixpath
import re
import shlex
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: ``auto_approved_reason`` for calls approved by a flow's approval policy.
FLOW_APPROVAL_POLICY_REASON = "flow_approval_policy"

MODE_ASK = "ask"
MODE_AUTO_ALLOW = "auto_allow"

#: Native tools a policy may auto-approve. Network tools (WebFetch,
#: WebSearch) and MCP tools are not eligible.
POLICY_TOOLS = frozenset(
    {
        "Bash",
        "Read",
        "Edit",
        "MultiEdit",
        "Write",
        "Glob",
        "Grep",
        "LS",
        "NotebookEdit",
        "TodoWrite",
    }
)

#: Placeholder for the directory the flow runs in on the host.
WORKSPACE_PLACEHOLDER = "<workspace>"

_FILE_PATH_KEYS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Write": ("file_path",),
    "NotebookEdit": ("notebook_path",),
}
_SEARCH_PATH_KEYS = {"Glob": ("path",), "Grep": ("path",), "LS": ("path",)}

#: Shell commands that a policy never auto-approves: publishing, network
#: access and privilege changes always go to a human.
_BASH_DENY = re.compile(
    r"(?:^|[\s;&|(`$])(?:"
    r"git\b[^;&|\n]*\s(?:push|remote\s+(?:add|set-url)|fetch|pull|clone)"
    r"|curl|wget|ssh|scp|sftp|rsync|nc|ncat|netcat|telnet|ftp|socat"
    r"|gh|glab|npm\s+publish|yarn\s+publish|pnpm\s+publish|twine|docker\s+push"
    r"|sudo|su|doas|chmod\s+[0-7]*s|chown"
    r")(?=$|[\s;&|)`])"
)


@dataclass(frozen=True)
class FlowApprovalGrant:
    """An active flow approval policy bound to one native session."""

    flow_id: Any
    flow_name: str
    execution_id: str
    tools: frozenset[str]
    paths: tuple[str, ...]


def validate_approval_policy(value: Any) -> Dict[str, Any]:
    """Validate ``agent_config.approval_policy``.

    Args:
        value: The raw policy.

    Returns:
        The normalized policy.

    Raises:
        ValueError: When the policy is malformed.
    """
    if not isinstance(value, Mapping):
        raise ValueError("agent_config.approval_policy must be an object")
    unknown = sorted(str(key) for key in value if key not in {"mode", "tools", "paths"})
    if unknown:
        raise ValueError(
            "agent_config.approval_policy has unsupported keys: " + ", ".join(unknown)
        )
    mode = value.get("mode", MODE_ASK)
    if mode not in {MODE_ASK, MODE_AUTO_ALLOW}:
        raise ValueError(
            'agent_config.approval_policy.mode must be "ask" or "auto_allow"'
        )
    tools = value.get("tools", [])
    paths = value.get("paths", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError(
            "agent_config.approval_policy.tools must be a list of tool names"
        )
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        raise ValueError("agent_config.approval_policy.paths must be a list of paths")
    bad_tools = sorted(set(tools) - POLICY_TOOLS)
    if bad_tools:
        raise ValueError(
            "agent_config.approval_policy.tools may only list "
            + ", ".join(sorted(POLICY_TOOLS))
            + "; not "
            + ", ".join(bad_tools)
        )
    if len(paths) > 16:
        raise ValueError("agent_config.approval_policy.paths allows at most 16 entries")
    normalized_paths = []
    for raw in paths:
        if raw == WORKSPACE_PLACEHOLDER:
            normalized_paths.append(raw)
            continue
        if not raw.startswith("/") or any(c in raw for c in ("\x00", "\n", "\r")):
            raise ValueError(
                "agent_config.approval_policy.paths entries must be absolute "
                f"paths or {WORKSPACE_PLACEHOLDER}"
            )
        normalized = posixpath.normpath(raw)
        if normalized == "/":
            raise ValueError("agent_config.approval_policy.paths cannot be /")
        normalized_paths.append(normalized)
    if mode == MODE_AUTO_ALLOW and (not tools or not normalized_paths):
        raise ValueError(
            "agent_config.approval_policy with mode auto_allow needs tools and paths"
        )
    return {"mode": mode, "tools": list(tools), "paths": normalized_paths}


def _within(path: str, bases: Iterable[str]) -> bool:
    if not path.startswith("/"):
        return False
    normalized = posixpath.normpath(path)
    for base in bases:
        if normalized == base or normalized.startswith(base.rstrip("/") + "/"):
            return True
    return False


def _bash_within_policy(command: str, cwd: Optional[str], paths: Sequence[str]) -> bool:
    if not cwd or not _within(cwd, paths):
        return False
    if _BASH_DENY.search(command):
        return False
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        return False
    for token in tokens:
        for piece in re.split(r"[=:]", token):
            if piece.startswith("~"):
                return False
            if ".." in piece.split("/"):
                return False
            if piece.startswith("/") and not _within(piece, paths):
                return False
    return True


def tool_call_within_policy(
    grant: FlowApprovalGrant,
    *,
    tool_name: str,
    tool_input: Mapping[str, Any],
    cwd: Optional[str],
) -> bool:
    """Whether a native tool call is covered by the flow's policy.

    Args:
        grant: The active grant.
        tool_name: Native tool name.
        tool_input: Native tool arguments.
        cwd: The session's working directory as the hook reported it.

    Returns:
        True to auto-approve; False sends the call down the normal path.
    """
    if tool_name not in grant.tools or not grant.paths:
        return False
    if tool_name == "Bash":
        command = tool_input.get("command")
        return isinstance(command, str) and _bash_within_policy(
            command, cwd, grant.paths
        )
    if tool_name in _FILE_PATH_KEYS:
        values = [tool_input.get(key) for key in _FILE_PATH_KEYS[tool_name]]
        present = [value for value in values if value is not None]
        return bool(present) and all(
            isinstance(value, str) and _within(value, grant.paths) for value in present
        )
    if tool_name in _SEARCH_PATH_KEYS:
        if not cwd or not _within(cwd, grant.paths):
            return False
        for key in _SEARCH_PATH_KEYS[tool_name]:
            value = tool_input.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return False
            target = value if value.startswith("/") else posixpath.join(cwd, value)
            if not _within(target, grant.paths):
                return False
        return True
    if tool_name == "TodoWrite":
        return True
    return False


def _flow_workspace_path(agent_config: Mapping[str, Any]) -> Optional[str]:
    workspace = agent_config.get("workspace")
    if isinstance(workspace, Mapping) and workspace.get("kind") == "path":
        path = workspace.get("path")
        if isinstance(path, str) and path.startswith("/"):
            return posixpath.normpath(path)
    return None


def resolve_flow_grant(
    db: Session,
    *,
    account_id: str,
    managed_agent_id: Any,
    execution_id: str,
    session_id: Optional[str],
    cwd: Optional[str],
) -> Optional[FlowApprovalGrant]:
    """Return the flow approval grant that covers this hook call, if any.

    The first call that presents the execution id binds the grant to its
    native session id (and, for ``<workspace>``, its cwd). Later calls from
    another session get no grant.

    Args:
        db: Database session.
        account_id: Caller's account.
        managed_agent_id: Managed agent the caller authenticated as.
        execution_id: Flow execution id the hook reported.
        session_id: Native session id the hook reported.
        cwd: Working directory the hook reported.

    Returns:
        The grant, or None when no policy applies.
    """
    from uuid import UUID

    from preloop.models.crud import crud_flow, crud_flow_execution
    from preloop.services.runner_service import unwrap_agent_config

    if not session_id or managed_agent_id is None:
        return None
    try:
        execution_uuid = UUID(str(execution_id))
    except ValueError:
        return None
    execution = crud_flow_execution.get(db, id=execution_uuid)
    if execution is None:
        return None
    if (
        str(execution.status or "").upper()
        in crud_flow_execution.TERMINAL_EXECUTION_STATUSES
    ):
        return None
    # Account scoping: the flow lookup is account scoped.
    flow = crud_flow.get(db, id=execution.flow_id, account_id=str(account_id))
    if flow is None:
        return None
    config = unwrap_agent_config(flow.agent_config)
    if not isinstance(config, dict) or config.get("execution_path") != "persistent":
        return None
    try:
        policy = validate_approval_policy(config.get("approval_policy") or {})
    except ValueError:
        logger.warning("Ignoring invalid approval policy on flow %s", flow.id)
        return None
    if policy["mode"] != MODE_AUTO_ALLOW:
        return None
    binding = crud_flow_execution.bind_approval_session(
        db,
        execution_id=execution_uuid,
        account_id=str(account_id),
        managed_agent_id=str(managed_agent_id),
        session_id=session_id,
        cwd=cwd,
    )
    if binding is None or binding.get("approval_session_id") != session_id:
        return None
    workspace = _flow_workspace_path(config) or binding.get("approval_cwd")
    paths = []
    for entry in policy["paths"]:
        if entry == WORKSPACE_PLACEHOLDER:
            if isinstance(workspace, str) and workspace.startswith("/"):
                normalized = posixpath.normpath(workspace)
                if normalized != "/":
                    paths.append(normalized)
            continue
        paths.append(entry)
    return FlowApprovalGrant(
        flow_id=flow.id,
        flow_name=str(flow.name or "flow"),
        execution_id=str(execution_uuid),
        tools=frozenset(policy["tools"]),
        paths=tuple(paths),
    )
