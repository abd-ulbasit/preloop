package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDevinManagedArgs(t *testing.T) {
	profile := hostExecProfile{Name: "devin", Executable: "devin", ModelMap: map[string]string{"fast": "sonnet"}}
	args, err := buildDevinHostExecArgs(profile, map[string]any{"prompt": "--cloud", "model_identifier": "fast"}, "/tmp/result.json", "abc123")
	if err != nil {
		t.Fatal(err)
	}
	got := strings.Join(args, "|")
	for _, want := range []string{"--permission-mode|auto", "--export|/tmp/result.json", "--resume|abc123", "--model|sonnet", "--|--cloud"} {
		if !strings.Contains(got, want) {
			t.Errorf("missing %s in %s", want, got)
		}
	}
	for _, arg := range []string{"--cloud", "--config=x", "-p", "--permission-mode=dangerous", "acp"} {
		profile.Argv = []string{arg}
		if _, err := normalizeHostExecProfile(profile); err == nil {
			t.Errorf("accepted %s", arg)
		}
	}
}

func TestDevinExportRequiresCompletedAgentStep(t *testing.T) {
	path := filepath.Join(t.TempDir(), "export.json")
	for _, doc := range []string{`{}`, `{"schema_version":"ATIF-v1.0","session_id":"abc","steps":[{"source":"user","message":"prompt"}]}`, `{"schema_version":"ATIF-v1.0","session_id":"abc","steps":[{"source":"agent","message":"calling","tool_calls":[{}]}]}`} {
		if err := os.WriteFile(path, []byte(doc), 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := readDevinExport(path); err == nil {
			t.Errorf("accepted incomplete export %s", doc)
		}
	}
	doc := `{"schema_version":"ATIF-v1.0","session_id":"abc","agent":{"name":"devin","model_name":"sonnet"},"steps":[{"source":"agent","message":"Done"}]}`
	if err := os.WriteFile(path, []byte(doc), 0600); err != nil {
		t.Fatal(err)
	}
	export, err := readDevinExport(path)
	if err != nil {
		t.Fatal(err)
	}
	if export.result()["harness"] != "devin_cli" || export.SessionID != "abc" {
		t.Fatalf("wrong export: %+v", export)
	}
}

func TestDevinHooksPreserveSettings(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("APPDATA", t.TempDir())
	path, err := devinConfigPath("config.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(`{ /* existing settings */ "model":"sonnet"}`), 0600); err != nil {
		t.Fatal(err)
	}
	command := "/usr/bin/preloop agents permission-hook --source devin_cli"
	for _, event := range []string{"PreToolUse", "PermissionRequest"} {
		if err := upsertNestedCommandHook(path, event, "", command, 300); err != nil {
			t.Fatal(err)
		}
	}
	if !devinApprovalHooksInstalled() {
		t.Fatal("hooks not detected")
	}
	for _, event := range []string{"PreToolUse", "PermissionRequest"} {
		if err := removeNestedCommandHook(path, event, false); err != nil {
			t.Fatal(err)
		}
	}
	raw, _ := os.ReadFile(path)
	if !strings.Contains(string(raw), "sonnet") || devinApprovalHooksInstalled() {
		t.Fatalf("incorrect removal: %s", raw)
	}
	req, err := buildPermissionRequest(permissionSourceDevinCLI, []byte(`{"tool_name":"exec","tool_input":{"command":"pwd"},"session_id":"abc"}`), permissionHookCredential{})
	if err != nil || req.ToolName != "exec" || req.ClientDecision != "ask" {
		t.Fatalf("request %+v: %v", req, err)
	}
	if renderHookDecision(permissionSourceDevinCLI, hookDecision{Behavior: "ask"})["decision"] != "block" {
		t.Fatal("ask must fail closed")
	}
}

func TestDevinSessionCreatesAndResumes(t *testing.T) {
	skipNoShebangOnWindows(t, "fake Devin CLI")
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("APPDATA", t.TempDir())
	path, _ := devinConfigPath("config.json")
	for _, event := range []string{"PreToolUse", "PermissionRequest"} {
		if err := upsertNestedCommandHook(path, event, "", "preloop agents permission-hook --source devin_cli", 300); err != nil {
			t.Fatal(err)
		}
	}
	dir := t.TempDir()
	script := `#!/bin/sh
export_path=
resume=
while [ "$#" -gt 0 ]; do
 case "$1" in
 --export) export_path="$2"; shift ;;
 --resume) resume="$2"; shift ;;
 esac
 shift
done
[ -z "$resume" ] || [ "$resume" = session-123 ] || exit 2
printf '%s' '{"schema_version":"ATIF-v1.0","session_id":"session-123","agent":{"name":"devin"},"steps":[{"source":"agent","message":"Done"}]}' > "$export_path"
`
	if err := os.WriteFile(filepath.Join(dir, "devin"), []byte(script), 0755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	adapter := &devinSessionAdapter{}
	if err := adapter.Start(&runnerRemoteSession{}); err != nil {
		t.Fatal(err)
	}
	var events []string
	turn := runnerSessionTurnSpec{Text: "hello", Dir: realTempDir(t)}
	emit := func(kind string, payload map[string]any) {
		events = append(events, kind)
		if kind == "agent_message" && payload["text"] != "Done" {
			t.Errorf("wrong text: %v", payload)
		}
	}
	result := adapter.Turn(context.Background(), turn, emit)
	if result.Status != "ok" || result.HarnessSessionID != "session-123" {
		t.Fatalf("first turn: %+v", result)
	}
	turn.Resume = true
	turn.HarnessSessionID = result.HarnessSessionID
	result = adapter.Turn(context.Background(), turn, emit)
	if result.Status != "ok" || len(events) != 2 {
		t.Fatalf("resume: %+v, %v", result, events)
	}
}

func TestDevinModelDiscoveryKeepsIDsOnly(t *testing.T) {
	models := parseDevinModels([]byte(`{"families":[{"family_label":"Private data","variants":[{"model_uid":"swe-2-high","label":"Private label"},{"model_uid":"swe-2-high"},{"model_uid":"../invalid id"}]}]}`))
	if len(models) != 1 || models[0] != "swe-2-high" {
		t.Fatal(models)
	}
	profile := hostExecProfile{PassEnv: []string{"DEVIN_PERMISSION_MODE", "DEVIN_MODEL"}}
	env := hostExecChildEnv("darwin", hostExecHarnessDevin, profile, []string{"DEVIN_PERMISSION_MODE=dangerous", "DEVIN_MODEL=evil", "DEVIN_API_KEY=example"})
	if strings.Join(env, ",") != "DEVIN_API_KEY=example" {
		t.Fatalf("unsafe environment: %v", env)
	}
}

func TestDevinInventoryGeneratesRunnableProfile(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	t.Setenv("XDG_CONFIG_HOME", "")
	t.Setenv("APPDATA", t.TempDir())
	host := fakeHost{home: t.TempDir(), executables: map[string]bool{"devin": true}}
	deps := host.deps("linux")
	deps.DevinStatus = func(string) (string, []string) { return harnessLoginSignedIn, []string{"swe-2-high"} }
	inv := buildHarnessInventory(deps)
	entry := entryFor(t, inv, "devin_cli")
	if entry.SupportLevel != harnessSupportFlowsSessions || entry.SessionMode != "resume" || entry.GeneratedProfile == nil || entry.LoginState != harnessLoginSignedIn {
		t.Fatalf("entry: %+v", entry)
	}
	profiles := generatedHostExecProfiles(inv)
	if len(profiles) != 1 || profiles[0].Executable != "devin" || profiles[0].ModelMap["swe-2-high"] != "swe-2-high" {
		t.Fatalf("profiles: %+v", profiles)
	}
	path := filepath.Join(t.TempDir(), "mcp_config.json")
	if err := os.WriteFile(path, []byte(`{"mcpServers":{"existing":{"url":"https://example.com/mcp"}}}`), 0600); err != nil {
		t.Fatal(err)
	}
	servers, err := parseDevinMCP(path)
	if err != nil || len(servers) != 1 {
		t.Fatalf("MCP: %v %v", servers, err)
	}
	missing := AgentConfig{Name: devinAgentName, ConfigPath: filepath.Join(t.TempDir(), "mcp_config.json")}
	if _, err := buildManagedMCPEnrollmentPlan(missing, "https://example.com", "example-token"); err != nil {
		t.Fatal(err)
	}
}

func TestDevinModelProbePreservesLargeCatalog(t *testing.T) {
	skipNoShebangOnWindows(t, "fake Devin CLI")
	variants := []map[string]string{
		{"model_uid": "swe-2-high", "label": "Private label"},
		{"model_uid": "swe-2-high"},
		{"model_uid": "../invalid id"},
	}
	for i := 0; i < 838; i++ {
		variants = append(variants, map[string]string{"model_uid": fmt.Sprintf("example-model-%03d", i), "description": strings.Repeat("catalog metadata ", 32)})
	}
	raw, err := json.Marshal(map[string]any{"families": []any{map[string]any{"variants": variants}}})
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) <= 256*1024 {
		t.Fatal("fixture does not exercise former capture limit")
	}
	models := probeDevinModels(fakeDevinModelCatalog(t, raw, 0))
	if len(models) != maxHarnessModels || models[0] != "swe-2-high" || models[1] != "example-model-000" || models[len(models)-1] != fmt.Sprintf("example-model-%03d", maxHarnessModels-2) {
		t.Fatalf("wrong bounded catalog: %v", models)
	}
}

func TestDevinModelProbeRejectsInvalidCatalogs(t *testing.T) {
	skipNoShebangOnWindows(t, "fake Devin CLI")
	valid := []byte(`{"families":[{"variants":[{"model_uid":"swe-2-high"}]}]}`)
	for _, tc := range []struct {
		name string
		raw  []byte
		exit int
	}{
		{"malformed", []byte(`{"families":[`), 0},
		{"trailing data", append(append([]byte{}, valid...), []byte(" extra")...), 0},
		{"failed command", valid, 1},
		// Padding leaves a valid small JSON document at the end. A suffix capture
		// would accept it despite exceeding the complete-document bound.
		{"oversized", append([]byte(strings.Repeat(" ", 4*1024*1024)), valid...), 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if models := probeDevinModels(fakeDevinModelCatalog(t, tc.raw, tc.exit)); len(models) != 0 {
				t.Fatalf("accepted invalid catalog: %v", models)
			}
		})
	}
}

func fakeDevinModelCatalog(t *testing.T, raw []byte, exit int) string {
	t.Helper()
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "catalog.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
	executable := filepath.Join(dir, "devin")
	script := fmt.Sprintf(`#!/bin/sh
[ "$*" = "models list --format json" ] || exit 2
cat "$(dirname "$0")/catalog.json"
exit %d
`, exit)
	if err := os.WriteFile(executable, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	return executable
}

func TestDevinModelCatalogOutputBound(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	out := &devinModelCatalogOutput{cancel: cancel}
	chunk := []byte(strings.Repeat("x", maxDevinModelCatalogBytes/2))
	for i := 0; i < 2; i++ {
		if n, err := out.Write(chunk); n != len(chunk) || err != nil {
			t.Fatalf("write: %d, %v", n, err)
		}
	}
	if out.oversized || ctx.Err() != nil || out.buffer.Len() != maxDevinModelCatalogBytes {
		t.Fatal("output at the bound was rejected")
	}
	if n, err := out.Write([]byte("extra")); n != 5 || err != nil {
		t.Fatalf("overflow write: %d, %v", n, err)
	}
	if !out.oversized || ctx.Err() != context.Canceled || out.buffer.Len() != maxDevinModelCatalogBytes {
		t.Fatal("overflow did not cancel the probe and cap its buffer")
	}
	if _, err := out.Write(chunk); err != nil || out.buffer.Len() != maxDevinModelCatalogBytes {
		t.Fatal("output continued growing after cancellation")
	}
}
