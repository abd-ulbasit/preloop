package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func installCodexHostHook(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("CODEX_HOME", dir)
	doc := map[string]any{"hooks": map[string]any{"PreToolUse": []any{map[string]any{"matcher": "*", "hooks": []any{map[string]any{"type": "command", "command": "preloop agents permission-hook --source codex_cli --hook-event PreToolUse"}}}}}}
	raw, _ := json.Marshal(doc)
	if err := os.WriteFile(filepath.Join(dir, "hooks.json"), raw, 0600); err != nil {
		t.Fatal(err)
	}
}

func TestCodexHostExecManagedArgs(t *testing.T) {
	installCodexHostHook(t)
	profile := hostExecProfile{Executable: "codex", ModelMap: map[string]string{"local-model": "seat-model"}}
	prompt := "--dangerous \" & %path%\nnext"
	args, stdin, env, _, err := prepareCodexHostExec(profile, map[string]any{"prompt": prompt, "model_identifier": "local-model"}, t.TempDir(), t.TempDir(), "")
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	if stdin != prompt || strings.Contains(joined, prompt) || !strings.Contains(joined, "--sandbox workspace-write") || !strings.Contains(joined, "--model seat-model") || len(env) != 0 {
		t.Fatalf("invalid managed invocation: %v", args)
	}
	for _, flag := range []string{"--dangerously-bypass-approvals-and-sandbox", "--profile", "--config"} {
		if _, err := normalizeHostExecProfile(hostExecProfile{Name: "codex", Executable: "codex", Argv: []string{flag}}); err == nil {
			t.Fatalf("accepted %s", flag)
		}
	}
}

func TestCodexHostExecRequiresHook(t *testing.T) {
	t.Setenv("CODEX_HOME", t.TempDir())
	if _, _, _, _, err := prepareCodexHostExec(hostExecProfile{}, map[string]any{"prompt": "task"}, t.TempDir(), t.TempDir(), ""); err == nil {
		t.Fatal("missing governance accepted")
	}
}

func TestCodexStructuredCompletion(t *testing.T) {
	for _, tc := range []struct {
		name, events string
		wantErr      bool
		status       string
	}{
		{"success", `{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}` + "\n" + `{"type":"turn.completed","usage":{"input_tokens":12,"cached_input_tokens":3,"output_tokens":4}}`, false, "success"},
		{"failed", `{"type":"turn.failed","error":{"message":"denied"}}`, false, "failure"},
		{"error-before-completion", `{"type":"error"}` + "\n" + `{"type":"turn.completed"}`, false, "failure"},
		{"truncated", `{"type":"turn.started"}`, true, ""},
		{"duplicate", `{"type":"turn.completed"}` + "\n" + `{"type":"turn.completed"}`, true, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			b := &runnerLogBuffer{native: true, harness: hostExecHarnessCodex}
			_, _ = b.Write([]byte(tc.events))
			b.finish()
			result, err := nativeRunnerResult(b, nil)
			if (err != nil) != tc.wantErr {
				t.Fatalf("%v %v", result, err)
			}
			if err == nil && result["status"] != tc.status {
				t.Fatal(result)
			}
		})
	}
}

func TestCodexHostExecMCPTokenNeverInArgv(t *testing.T) {
	installCodexHostHook(t)
	t.Setenv("PRELOOP_URL", "https://example.invalid")
	args, _, env, files, err := prepareCodexHostExec(hostExecProfile{}, map[string]any{"prompt": "task"}, t.TempDir(), t.TempDir(), "synthetic-scoped-token")
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(strings.Join(args, " "), "synthetic-scoped-token") || len(files) != 0 || !strings.Contains(strings.Join(env, "\n"), "PRELOOP_HOST_MCP_TOKEN=synthetic-scoped-token") {
		t.Fatal("MCP token transport violated")
	}
	if !strings.Contains(strings.Join(args, " "), "mcp_servers.preloop-flow.required=true") {
		t.Fatal("required MCP startup not enforced")
	}
}
