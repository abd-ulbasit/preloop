package cmd

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/testenv"
)

func TestCopilotMissingConfigCanBeOnboarded(t *testing.T) {
	path := filepath.Join(t.TempDir(), ".copilot", "mcp-config.json")
	agent := AgentConfig{Name: copilotCLIAgentName, ConfigPath: path}
	if name := defaultAgentDisplayName(agent); name != "Copilot CLI" {
		t.Fatalf("name: %q", name)
	}
	plan, err := buildManagedMCPEnrollmentPlan(agent, "https://example.com", "example-token")
	if err != nil {
		t.Fatal(err)
	}
	if reflect.ValueOf(plan).IsZero() {
		t.Fatal("empty enrollment plan")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Fatal("planning should not write the config")
	}
}

func TestDockerCapabilityRequiresReachableDaemon(t *testing.T) {
	if os.PathSeparator == '\\' {
		t.Skip("POSIX fixture")
	}
	dir := t.TempDir()
	t.Setenv("PATH", dir)
	path := filepath.Join(dir, "docker")
	for _, tc := range []struct {
		exit string
		want bool
	}{{"0", true}, {"1", false}} {
		script := "#!/bin/sh\n[ \"$1\" = ps ] || exit 2\nexit " + tc.exit + "\n"
		if err := os.WriteFile(path, []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
		if got := dockerAvailable(); got != tc.want {
			t.Fatalf("dockerAvailable=%v, want %v", got, tc.want)
		}
	}
}

func TestCursorSessionArgsResumeAndKeepPromptSeparate(t *testing.T) {
	args, err := cursorSessionArgs(hostExecProfile{Executable: "cursor-agent"}, runnerSessionTurnSpec{HarnessSessionID: "chat-123", Text: "--force", Model: "sonnet", Dir: "/tmp/workspace"})
	if err != nil {
		t.Fatal(err)
	}
	joined := strings.Join(args, " ")
	if !strings.Contains(joined, "--resume chat-123") || !strings.Contains(joined, "--model sonnet") {
		t.Fatal(args)
	}
	if args[len(args)-2] != "--" || args[len(args)-1] != "--force" {
		t.Fatal(args)
	}
	if _, err := cursorSessionArgs(hostExecProfile{}, runnerSessionTurnSpec{HarnessSessionID: "../escape"}); err == nil {
		t.Fatal("invalid chat id accepted")
	}
}

func TestCursorSessionsRequireApprovalHooks(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	err := (&cursorSessionAdapter{}).Start(&runnerRemoteSession{})
	if err == nil || !strings.Contains(err.Error(), "cursor_approval_hook_missing") {
		t.Fatalf("got %v", err)
	}
}

func TestCursorSessionCreatesChatAndRunsResumedTurn(t *testing.T) {
	skipNoShebangOnWindows(t, "fake Cursor CLI")
	home := testenv.SetTempHome(t)
	hooksDir := filepath.Join(home, ".cursor")
	if err := os.MkdirAll(hooksDir, 0o700); err != nil {
		t.Fatal(err)
	}
	hooks := `{"hooks":{"preToolUse":[{"command":"preloop agents permission-hook --source cursor"}],"beforeShellExecution":[{"command":"preloop agents permission-hook --source cursor"}],"beforeMCPExecution":[{"command":"preloop agents permission-hook --source cursor"}]}}`
	if err := os.WriteFile(filepath.Join(hooksDir, "hooks.json"), []byte(hooks), 0o600); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	script := "#!/bin/sh\nif [ \"$1\" = create-chat ]; then echo chat-123; exit 0; fi\necho '{\"type\":\"assistant\",\"message\":{\"content\":\"ok\"}}'\necho '{\"type\":\"result\",\"session_id\":\"chat-123\",\"is_error\":false}'\n"
	if err := os.WriteFile(filepath.Join(dir, "cursor-agent"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	adapter := &cursorSessionAdapter{}
	session := &runnerRemoteSession{}
	if err := adapter.Start(session); err != nil {
		t.Fatal(err)
	}
	if session.HarnessSessionID != "chat-123" {
		t.Fatal(session.HarnessSessionID)
	}
	var events []string
	result := adapter.Turn(context.Background(), runnerSessionTurnSpec{HarnessSessionID: session.HarnessSessionID, Text: "hello", Dir: realTempDir(t)}, func(kind string, _ map[string]any) { events = append(events, kind) })
	if result.Status != "ok" || !result.SessionCreated {
		t.Fatalf("result: %+v", result)
	}
	if !reflect.DeepEqual(events, []string{"agent_message", "usage"}) {
		t.Fatal(events)
	}
}
