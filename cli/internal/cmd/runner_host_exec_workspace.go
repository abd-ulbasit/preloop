package cmd

import (
	"fmt"
)

// Host-exec flows in an authorized directory (issue #1669). A flow may name
// one of the directories the runner owner authorized with `preloop runner
// dirs add` (agent_config.workspace = {"kind": "authorized_directory",
// "id": ..., "allow_dirty": bool}). The control plane sends only the id; the
// runner resolves it with the same policy as runner sessions
// (resolveSessionWorkspace): the entry must exist and allow the harness, a
// read_only entry is refused, the filesystem root and the home directory
// are refused, and a git tree with uncommitted changes is refused unless the
// flow sets allow_dirty.

const hostExecWorkspaceKey = "host_exec_workspace"

// jobHostExecWorkspace decodes the delivered workspace, or nil when the run
// uses a fresh execution directory.
func jobHostExecWorkspace(job map[string]any) (*sessionWorkspaceSpec, error) {
	raw, ok := job[hostExecWorkspaceKey]
	if !ok || raw == nil {
		return nil, nil
	}
	obj, ok := raw.(map[string]any)
	if !ok {
		return nil, workspaceError(workspaceErrInvalid, "host_exec_workspace must be an object")
	}
	for key := range obj {
		switch key {
		case "kind", "id", "allow_dirty":
		default:
			return nil, workspaceError(workspaceErrInvalid, "host_exec_workspace has unsupported key %q", key)
		}
	}
	if kind, _ := obj["kind"].(string); kind != workspaceKindAuthorizedDirectory {
		return nil, workspaceError(workspaceErrInvalid, "host execution supports only authorized_directory workspaces")
	}
	id, _ := obj["id"].(string)
	if !authorizedDirIDRe.MatchString(id) {
		return nil, workspaceError(workspaceErrNotAuthorized, "directory id is invalid")
	}
	allowDirty := false
	if value, present := obj["allow_dirty"]; present {
		flag, ok := value.(bool)
		if !ok {
			return nil, workspaceError(workspaceErrInvalid, "host_exec_workspace.allow_dirty must be a boolean")
		}
		allowDirty = flag
	}
	return &sessionWorkspaceSpec{Kind: workspaceKindAuthorizedDirectory, ID: id, AllowDirty: allowDirty}, nil
}

// hostExecMCPConfigByFlag reports whether the harness reads its per-job MCP
// configuration from a path passed on the command line. Cursor and Devin
// read it from a fixed location inside the working directory, which in an
// authorized directory is the operator's own project.
func hostExecMCPConfigByFlag(harness string) bool {
	return harness == hostExecHarnessClaude || harness == hostExecHarnessCopilot
}

// validateHostExecWorkspaceJob refuses combinations that would write into
// the operator's directory: a repository checkout, or an MCP config file a
// harness can only read from its working directory.
func validateHostExecWorkspaceJob(spec *sessionWorkspaceSpec, harness string, checkout *hostExecCheckout, mcpToken string) error {
	if spec == nil {
		return nil
	}
	if checkout != nil {
		return fmt.Errorf("host_exec_workspace_conflict: a flow that runs in an authorized directory cannot also clone repositories")
	}
	if mcpToken != "" && !hostExecMCPConfigByFlag(harness) {
		return fmt.Errorf("host_exec_workspace_conflict: %s reads MCP configuration from its working directory; remove the flow's Preloop tools or use a managed workspace", harness)
	}
	return nil
}
