package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/testenv"
	"github.com/spf13/cobra"
)

func TestCodexNoteLifecycleInstallAndRemove(t *testing.T) {
	home := testenv.SetTempHome(t)
	agent := AgentConfig{Name: "Codex CLI"}
	if err := installApprovalHooks(agent, "https://example.com", "agt_synthetic", nil); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, ".codex", "hooks.json")
	for _, event := range []string{"Stop", "SessionEnd"} {
		if commands := nestedHookCommands(t, readJSONDoc(t, path), event); !containsSubstring(commands, "permission-hook") {
			t.Errorf("missing %s: %v", event, commands)
		}
	}
	if err := removeApprovalHooks(agent, nil); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(path); os.IsNotExist(err) {
		return
	}
	for _, event := range []string{"Stop", "SessionEnd"} {
		if commands := nestedHookCommands(t, readJSONDoc(t, path), event); containsSubstring(commands, "permission-hook") {
			t.Errorf("retained %s", event)
		}
	}
}

func TestCursorSessionAdditionalContextShape(t *testing.T) {
	var out bytes.Buffer
	writeSessionAdditionalContext(&out, "sessionStart", "operator instruction")
	var decoded map[string]interface{}
	if err := json.Unmarshal(out.Bytes(), &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["additional_context"] != "operator instruction" {
		t.Fatalf("Cursor needs additional_context, got %s", out.String())
	}
}

func TestNonCarryingLifecycleEventsDoNotClaimNotes(t *testing.T) {
	for _, source := range []string{permissionSourceCursor, permissionSourceCopilotCLI} {
		t.Run(source, func(t *testing.T) {
			stub, home := newNoteHookStub(t)
			cred, _ := resolvePermissionHookCredential(permissionSourceClaudeCode)
			cred.Source = source
			writeTestPermissionCredential(t, home, "agent-other", cred)
			cmd := &cobra.Command{}
			cmd.Flags().String("source", source, "")
			cmd.SetIn(strings.NewReader(`{"session_id":"sess-no-carry","hook_event_name":"beforeSubmitPrompt","prompt":"hello"}`))
			cmd.SetOut(&bytes.Buffer{})
			cmd.SetErr(&bytes.Buffer{})
			if err := runAgentsSessionHook(cmd, nil); err != nil {
				t.Fatal(err)
			}
			if len(stub.pulls()) != 0 {
				t.Fatal("non-carrying event claimed notes")
			}
		})
	}
}

func TestCursorStopUsesFollowupMessageAndLoopGuard(t *testing.T) {
	stub, home := newNoteHookStub(t)
	stub.pendingText = "<operator-notes>Run checks</operator-notes>"
	cred, _ := resolvePermissionHookCredential(permissionSourceClaudeCode)
	cred.Source = permissionSourceCursor
	writeTestPermissionCredential(t, home, "agent-cursor", cred)
	run := func(payload string) string {
		cmd := &cobra.Command{}
		cmd.Flags().String("source", permissionSourceCursor, "")
		cmd.SetIn(strings.NewReader(payload))
		var out bytes.Buffer
		cmd.SetOut(&out)
		cmd.SetErr(&bytes.Buffer{})
		if err := runAgentsStopHook(cmd, nil); err != nil {
			t.Fatal(err)
		}
		return out.String()
	}
	out := run(`{"conversation_id":"cursor-synthetic","status":"completed","loop_count":0}`)
	var decoded map[string]interface{}
	if err := json.Unmarshal([]byte(out), &decoded); err != nil {
		t.Fatal(err)
	}
	if decoded["followup_message"] != stub.pendingText || len(decoded) != 1 {
		t.Fatalf("wrong Cursor output: %s", out)
	}
	for _, payload := range []string{
		`{"conversation_id":"cursor-synthetic","status":"completed","loop_count":3}`,
		`{"conversation_id":"cursor-synthetic","status":"aborted","loop_count":0}`,
	} {
		if out := run(payload); out != "" {
			t.Fatalf("must not continue capped or aborted conversation: %s", out)
		}
	}
	if len(stub.pulls()) != 1 {
		t.Fatal("guarded Cursor stop claimed notes")
	}
}

func TestSessionEndWithNoIdentityCannotEndBoundSession(t *testing.T) {
	stub, _ := newNoteHookStub(t)
	runNoteHook(t, runAgentsSessionEndHook, `{"hook_event_name":"SessionEnd"}`)
	if len(stub.sessionEnd) != 0 {
		t.Fatal("empty session id must not end token-bound session")
	}
}

func TestCopilotSessionEndUsesNativeSessionId(t *testing.T) {
	stub, home := newNoteHookStub(t)
	cred, _ := resolvePermissionHookCredential(permissionSourceClaudeCode)
	cred.Source = permissionSourceCopilotCLI
	writeTestPermissionCredential(t, home, "agent-copilot", cred)
	cmd := &cobra.Command{}
	cmd.Flags().String("source", permissionSourceCopilotCLI, "")
	cmd.SetIn(strings.NewReader(`{"sessionId":"copilot-synthetic","reason":"complete"}`))
	cmd.SetOut(&bytes.Buffer{})
	cmd.SetErr(&bytes.Buffer{})
	if err := runAgentsSessionEndHook(cmd, nil); err != nil {
		t.Fatal(err)
	}
	if len(stub.sessionEnd) != 1 || stub.sessionEnd[0]["external_session_id"] != "copilot-synthetic" {
		t.Fatalf("wrong native end: %v", stub.sessionEnd)
	}
}

func TestClaudeNoteLifecycleDoesNotRunAsCursorThirdPartyHook(t *testing.T) {
	stub, _ := newNoteHookStub(t)
	original := permissionHookGetenv
	t.Cleanup(func() { permissionHookGetenv = original })
	permissionHookGetenv = func(key string) string {
		if key == "CURSOR_AGENT" {
			return "1"
		}
		return ""
	}
	runNoteHook(t, runAgentsStopHook, `{"session_id":"cursor-synthetic","hook_event_name":"Stop"}`)
	runNoteHook(t, runAgentsSessionEndHook, `{"session_id":"cursor-synthetic","hook_event_name":"SessionEnd"}`)
	if len(stub.pulls()) != 0 || len(stub.sessionEnd) != 0 {
		t.Fatal("third-party Claude hooks must not claim notes or end Cursor sessions")
	}
}
