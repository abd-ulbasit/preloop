package cmd

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"time"
)

// inventoryControl keeps adapter support, local configuration and execution
// evidence independent. All values come from fixed vocabularies, never config.
type inventoryControl struct {
	Axis                  string `json:"axis"`
	Supported             string `json:"supported"`
	Configured            string `json:"configured"`
	ConfigurationEvidence string `json:"configuration_evidence"`
	Verification          string `json:"verification"`
	EvidenceKind          string `json:"evidence_kind"`
}

// Freshness scaffolding for a future app-harness caller: there is no prior-
// verification loader or writer in this collector. Offline inventory always
// reports unverified/unavailable, including when old enrollment probes passed.
// Revisions are opaque, local verifier inputs, not serialized inventory fields.
// Unknown revisions cannot establish freshness. Never read credentials to build
// these during offline collection; it has no verification evidence to reuse.
type inventoryVerificationContext struct {
	Config, App, Policy, Credential string
}

type inventoryVerification struct {
	Context                    inventoryVerificationContext
	Axis, Status, EvidenceKind string
	VerifiedAt, ExpiresAt      time.Time
}

func inventoryVerificationState(axis string, prior *inventoryVerification, current inventoryVerificationContext, now time.Time) (string, string) {
	if prior == nil || prior.Axis != axis || (prior.Status != "passed" && prior.Status != "failed") {
		return "unverified", "unavailable"
	}
	// A direct probe establishes gateway route/accounting only. It cannot
	// establish any app control, even when its response says passed.
	if prior.EvidenceKind != "app_harness" {
		return "unverified", "unavailable"
	}
	if current.Config == "" || current.App == "" || current.Policy == "" || current.Credential == "" ||
		prior.Context != current || prior.VerifiedAt.IsZero() || prior.VerifiedAt.After(now) ||
		!prior.ExpiresAt.After(prior.VerifiedAt) || !now.Before(prior.ExpiresAt) {
		return "expired", "app_harness"
	}
	return prior.Status, "app_harness"
}

func inventoryControls(agent AgentConfig) []inventoryControl {
	caps := capabilitiesForAgent(agent)
	controls := make([]inventoryControl, 0, 3)
	for _, item := range []struct {
		axis    string
		support controlSupport
	}{
		{"model_route", caps.ModelRoute}, {"native_actions", caps.NativeActionGate}, {"managed_mcp", caps.ManagedMCP},
	} {
		supported := "unknown"
		if item.support == controlSupported {
			supported = "true"
		}
		if item.support == controlUnsupported {
			supported = "false"
		}
		verification, evidence := inventoryVerificationState(item.axis, nil, inventoryVerificationContext{}, time.Time{})
		controls = append(controls, inventoryControl{Axis: item.axis, Supported: supported, Configured: "unknown", ConfigurationEvidence: "unavailable", Verification: verification, EvidenceKind: evidence})
	}
	return controls
}

func inventorySetConfigured(controls []inventoryControl, axis, state string) {
	for i := range controls {
		if controls[i].Axis == axis {
			controls[i].Configured = state
			controls[i].ConfigurationEvidence = "local_config"
		}
	}
}

// Only explicit, recognized positive local configuration is promoted. Other
// adapters use policy files, plugins, managed OS sources, or environment state
// outside this collector's scope, so an absent field remains unknown.
func inventoryObserveLocalControls(controls []inventoryControl, appName string, data []byte) {
	if appName != "Claude Code" {
		return
	}
	decode := json.Unmarshal
	doc, ok := inventoryRawObject(decode, data)
	if !ok {
		return
	}
	env, _ := inventoryRawObject(decode, doc["env"])
	var base string
	if json.Unmarshal(env["ANTHROPIC_BASE_URL"], &base) == nil {
		gateway := inventoryHTTPURL(base)
		servers := inventoryLookupMCPContainer(doc, decode)
		entry, _ := inventoryRawObject(decode, servers["preloop"])
		var target string
		if json.Unmarshal(entry["url"], &target) == nil {
			mcp := inventoryHTTPURL(target)
			if gateway != nil && mcp != nil && gateway.Scheme == mcp.Scheme && gateway.Host == mcp.Host && strings.TrimRight(gateway.Path, "/") == "/anthropic" && strings.TrimRight(mcp.Path, "/") == "/mcp/v1" {
				inventorySetConfigured(controls, "model_route", "true")
			}
		}
	}
	// Do not decode arbitrary hook metadata or credentials. The shape matches
	// the current adapter's all-tools PreToolUse command. Consumption is unknown.
	var hooks struct {
		PreToolUse []struct {
			Matcher string `json:"matcher"`
			Hooks   []struct {
				Type    string `json:"type"`
				Command string `json:"command"`
			} `json:"hooks"`
		} `json:"PreToolUse"`
	}
	var disabled bool
	if json.Unmarshal(doc["disableAllHooks"], &disabled) == nil && disabled {
		return
	}
	if json.Unmarshal(doc["hooks"], &hooks) != nil {
		return
	}
	for _, group := range hooks.PreToolUse {
		if group.Matcher != "*" {
			continue
		}
		for _, hook := range group.Hooks {
			const suffix = " agents permission-hook --source " + permissionSourceClaudeCode
			if hook.Type != "command" || !strings.HasSuffix(hook.Command, suffix) {
				continue
			}
			executable := strings.TrimSuffix(hook.Command, suffix)
			if strings.ContainsAny(executable, "\r\n;&|`$<>") {
				continue
			}
			if strings.HasPrefix(executable, "\"") || strings.HasPrefix(executable, "'") {
				quote := executable[:1]
				if len(executable) < 2 || !strings.HasSuffix(executable, quote) {
					continue
				}
				executable = executable[1 : len(executable)-1]
				if strings.Contains(executable, quote) {
					continue
				}
			} else if strings.ContainsAny(executable, " \t\"'") {
				continue
			}
			executable = strings.ReplaceAll(executable, "\\", "/")
			if filepath.Base(executable) == "preloop" || filepath.Base(executable) == "preloop.exe" {
				inventorySetConfigured(controls, "native_actions", "true")
			}
		}
	}
}
