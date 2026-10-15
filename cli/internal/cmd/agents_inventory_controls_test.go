package cmd

import (
	"encoding/json"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestInventoryControlSupportIndependent(t *testing.T) {
	for _, tc := range []struct {
		name               string
		model, native, mcp string
	}{
		{"Cursor", "false", "true", "true"}, {"Copilot CLI", "false", "true", "true"},
		{"Claude Desktop", "true", "false", "true"}, {"Unknown app", "unknown", "unknown", "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			controls := inventoryControls(AgentConfig{Name: tc.name})
			for i, want := range []string{tc.model, tc.native, tc.mcp} {
				c := controls[i]
				if c.Supported != want || c.Configured != "unknown" || c.Verification != "unverified" || c.EvidenceKind != "unavailable" {
					t.Fatalf("support is not observation: %+v", c)
				}
			}
		})
	}
}

func TestInventoryVerificationFreshness(t *testing.T) {
	now := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	context := inventoryVerificationContext{"config-revision", "app-revision", "policy-revision", "credential-revision"}
	for _, axis := range []string{"model_route", "native_actions", "managed_mcp"} {
		t.Run(axis, func(t *testing.T) {
			record := inventoryVerification{Context: context, Axis: axis, Status: "passed", EvidenceKind: "app_harness", VerifiedAt: now.Add(-time.Minute), ExpiresAt: now.Add(time.Minute)}
			if status, _ := inventoryVerificationState(axis, &record, context, now); status != "passed" {
				t.Fatal(status)
			}
			record.Status = "failed"
			if status, _ := inventoryVerificationState(axis, &record, context, now); status != "failed" {
				t.Fatal(status)
			}
			for _, changed := range []inventoryVerificationContext{
				{"changed", context.App, context.Policy, context.Credential},
				{context.Config, "changed", context.Policy, context.Credential},
				{context.Config, context.App, "changed", context.Credential},
				{context.Config, context.App, context.Policy, "changed"},
				{},
			} {
				if status, _ := inventoryVerificationState(axis, &record, changed, now); status != "expired" {
					t.Fatalf("changed context reused: %s", status)
				}
			}
			if status, _ := inventoryVerificationState(axis, &record, context, record.ExpiresAt); status != "expired" {
				t.Fatal(status)
			}
			if status, _ := inventoryVerificationState(axis, &record, context, record.VerifiedAt.Add(-time.Second)); status != "expired" {
				t.Fatal(status)
			}
			record.EvidenceKind = "direct_gateway_probe"
			if status, _ := inventoryVerificationState(axis, &record, context, now); status != "unverified" {
				t.Fatalf("gateway probe promoted: %s", status)
			}
			record.EvidenceKind = "local_config"
			if status, _ := inventoryVerificationState(axis, &record, context, now); status != "unverified" {
				t.Fatalf("local config promoted: %s", status)
			}
		})
	}
}

func TestInventoryLocalControlsRemainUnverified(t *testing.T) {
	home := t.TempDir()
	writeInventoryFixture(t, home, ".claude/settings.json", `{"env":{"ANTHROPIC_BASE_URL":"https://example.com/anthropic","ANTHROPIC_AUTH_TOKEN":"synthetic-secret"},"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"/opt/example/preloop agents permission-hook --source claude_code"}]}]},"mcpServers":{"preloop":{"url":"https://example.com/mcp/v1"}}}`)
	scan := collectAgentInventory(inventoryTestDependencies(t, home))
	for _, app := range scan.Apps {
		if app.AppID == "claude-code" {
			for _, control := range app.Controls {
				if control.Configured != "true" || control.ConfigurationEvidence != "local_config" || control.Verification != "unverified" {
					t.Fatalf("independent config evidence: %+v", control)
				}
			}
			wire, _ := json.Marshal(app)
			for _, secret := range []string{"synthetic-secret", "example.com", "/opt/example", filepath.Join(home, ".claude")} {
				if strings.Contains(string(wire), secret) {
					t.Fatalf("leaked %s", wire)
				}
			}
			return
		}
	}
	t.Fatal("Claude Code missing")
}

func TestInventoryManagedConfigurationUncertainty(t *testing.T) {
	for _, tc := range []struct{ name, doc, want string }{
		{"unconfigured", `{}`, "false"},
		{"configured", `{"mcpServers":{"preloop":{"url":"https://example.com/mcp/v1"}}}`, "true"},
		{"stale", `{"mcpServers":{"preloop":{"url":"https://example.com/old"}}}`, "unknown"},
		{"malformed", `{"mcpServers":`, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			home := t.TempDir()
			writeInventoryFixture(t, home, ".cursor/mcp.json", tc.doc)
			scan := collectAgentInventory(inventoryTestDependencies(t, home))
			for _, app := range scan.Apps {
				if app.AppID == "cursor" {
					c := app.Controls[2]
					if c.Configured != tc.want || c.Verification != "unverified" {
						t.Fatalf("got %+v", c)
					}
					return
				}
			}
			t.Fatal("cursor missing")
		})
	}
}

func TestInventoryMalformedRouteContainerIsProbeFailure(t *testing.T) {
	home := t.TempDir()
	writeInventoryFixture(t, home, ".cursor/mcp.json", `{"mcpServers":false}`)
	scan := collectAgentInventory(inventoryTestDependencies(t, home))
	if scan.Completeness != "partial" {
		t.Fatal(scan.Completeness)
	}
	for _, probe := range scan.ProbeResults {
		if probe.AppID == "cursor" && probe.Kind == "config" && probe.Status == "error" && probe.ErrorCode == "config_malformed" {
			return
		}
	}
	t.Fatal("malformed route container had no failed config probe")
}

func TestInventoryHookRecognitionRejectsShellWrappers(t *testing.T) {
	for _, tc := range []struct {
		command string
		want    string
	}{
		{`/opt/example/preloop agents permission-hook --source claude_code`, "true"},
		{`"/opt/Example App/preloop" agents permission-hook --source claude_code`, "true"},
		{`false && /opt/example/preloop agents permission-hook --source claude_code`, "unknown"},
		{`echo /opt/example/preloop agents permission-hook --source claude_code`, "unknown"},
		{`/opt/Example App/preloop agents permission-hook --source claude_code`, "unknown"},
		{`$(echo /opt/example)/preloop agents permission-hook --source claude_code`, "unknown"},
	} {
		t.Run(tc.command, func(t *testing.T) {
			controls := inventoryControls(AgentConfig{Name: "Claude Code"})
			data, _ := json.Marshal(map[string]any{"hooks": map[string]any{"PreToolUse": []any{map[string]any{"matcher": "*", "hooks": []any{map[string]any{"type": "command", "command": tc.command}}}}}})
			inventoryObserveLocalControls(controls, "Claude Code", data)
			if controls[1].Configured != tc.want {
				t.Fatalf("got %+v", controls[1])
			}
		})
	}
}

func TestInventoryRecognizesInstalledApprovalHook(t *testing.T) {
	controls := inventoryControls(AgentConfig{Name: "Claude Code"})
	data, _ := json.Marshal(map[string]any{"hooks": map[string]any{"PreToolUse": []any{map[string]any{"matcher": "*", "hooks": []any{map[string]any{"type": "command", "command": strings.Replace(approvalHookCommand(permissionSourceClaudeCode), preloopExecutableForHooks(), "/opt/example/preloop", 1)}}}}}})
	inventoryObserveLocalControls(controls, "Claude Code", data)
	if controls[1].Configured != "true" || controls[1].Verification != "unverified" {
		t.Fatalf("installed hook not recognized: %+v", controls[1])
	}
}
