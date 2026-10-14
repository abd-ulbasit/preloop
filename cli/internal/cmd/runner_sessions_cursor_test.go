package cmd

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCursorSessionArgsTrustsAuthorizedWorkspaceWithoutForce(t *testing.T) {
	profile := hostExecProfile{Name: "cursor", Executable: "cursor-agent"}
	args, err := cursorSessionArgs(profile, runnerSessionTurnSpec{
		HarnessSessionID: sessTestID, Dir: "/authorized/workspace", Text: "--force", Model: "composer-2.5",
	})
	if err != nil {
		t.Fatal(err)
	}
	if cursorArgValue(args, "--workspace") != "/authorized/workspace" || cursorArgValue(args, "--resume") != sessTestID || cursorArgValue(args, "--model") != "composer-2.5" {
		t.Fatalf("managed arguments missing: %q", args)
	}
	trust := false
	for _, arg := range args {
		if arg == "--" {
			break
		}
		if arg == "--trust" {
			trust = true
		}
		if arg == "--force" || arg == "--yolo" || arg == "-f" {
			t.Fatalf("workspace trust must not enable automatic tool approval: %q", args)
		}
	}
	if !trust {
		t.Fatalf("Cursor requires explicit trust for a host-authorized workspace: %q", args)
	}
	if _, err := cursorSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: "--trust", Text: "x"}); err == nil {
		t.Fatal("flag-shaped session id accepted")
	}
}

func installFakeCursorSession(t *testing.T) (string, string) {
	t.Helper()
	skipNoShebangOnWindows(t, "Cursor session fake CLI")
	logPath := filepath.Join(t.TempDir(), "argv.log")
	_, _ = installFakeCursorAgent(t, `#!/bin/sh
printf '%s|%s\n' "$(pwd -P)" "$*" >> '`+logPath+`'
if [ "$1" = create-chat ]; then
 echo '`+sessTestID+`'
 exit 0
fi
trusted=false
for arg in "$@"; do
 case "$arg" in
  --) break ;;
  --trust) trusted=true ;;
  --force|--yolo|-f) echo 'unexpected automatic tool approval' >&2; exit 1 ;;
 esac
done
if [ "$trusted" != true ]; then echo 'Workspace Trust Required' >&2; exit 1; fi
echo '{"type":"result","subtype":"success","is_error":false,"session_id":"`+sessTestID+`","result":"ok"}'
`)
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := installApprovalHooks(cursorTestAgent(home), "https://example.com", "agent-example", nil); err != nil {
		t.Fatal(err)
	}
	writeHostExecProfiles(t, []hostExecProfile{{Name: "cursor", Executable: "cursor-agent"}})
	return logPath, filepath.Join(home, ".cursor", "hooks.json")
}

func TestCursorSessionAuthorizedWorkspaceAndResume(t *testing.T) {
	logPath, _ := installFakeCursorSession(t)
	dir := realTempDir(t)
	writeRunnerConfig(t, map[string]any{
		"harnesses":              map[string]any{"cursor_cli": map[string]any{"sessions_enabled": true}},
		"authorized_directories": []any{map[string]any{"id": "dir_9f2c", "path": dir, "label": "workspace", "mode": "write", "harnesses": []any{"cursor_cli"}}},
	})
	m := newRunnerSessionManager(nil)
	m.notifyHost = func(string) {}
	h := &sessionHarness{t: t, m: m}
	start := startMsg(sessTestID)
	start.Harness = "cursor_cli"
	start.FirstPrompt = "first turn"
	m.handle(start)
	msgs := h.waitFor(isTurnDone(runnerSessionFirstTurnID))
	if done := findMsg(msgs, isTurnDone(runnerSessionFirstTurnID)); done["status"] != "ok" {
		t.Fatalf("first turn failed: %v", msgs)
	}
	m.handle(runnerWSMessage{Type: "session_turn", RemoteSessionID: sessTestID, TurnID: "turn-2", Text: "second turn"})
	msgs = h.waitFor(isTurnDone("turn-2"))
	if done := findMsg(msgs, isTurnDone("turn-2")); done["status"] != "ok" {
		t.Fatalf("resumed turn failed: %v", msgs)
	}
	lines := readArgvLog(t, logPath)
	if len(lines) != 3 {
		t.Fatalf("want create-chat and two turns: %q", lines)
	}
	for _, line := range lines[1:] {
		if !strings.HasPrefix(line, dir+"|") || !strings.Contains(line, "--trust") || !strings.Contains(line, "--resume "+sessTestID) {
			t.Fatalf("unexpected Cursor invocation: %s", line)
		}
	}
	// Revoking authorization prevents another invocation even with a live chat.
	doc, err := loadRunnerConfigDoc()
	if err != nil {
		t.Fatal(err)
	}
	doc["authorized_directories"] = []any{}
	writeRunnerConfig(t, doc)
	m.handle(runnerWSMessage{Type: "session_turn", RemoteSessionID: sessTestID, TurnID: "turn-3", Text: "must not run"})
	msgs = h.waitFor(isTurnDone("turn-3"))
	if done := findMsg(msgs, isTurnDone("turn-3")); done["status"] != "error" || done["error_code"] != rejectWorkspaceNotAllowed {
		t.Fatalf("revoked workspace accepted: %v", msgs)
	}
	if lines := readArgvLog(t, logPath); len(lines) != 3 {
		t.Fatalf("Cursor ran after workspace authorization revoked: %q", lines)
	}
}

func TestCursorSessionRequiresAllApprovalHooksBeforeTrust(t *testing.T) {
	for _, event := range []string{"preToolUse", "beforeShellExecution", "beforeMCPExecution"} {
		t.Run(event, func(t *testing.T) {
			logPath, hooksPath := installFakeCursorSession(t)
			adapter := &cursorSessionAdapter{}
			session := &runnerRemoteSession{WorkspaceDir: realTempDir(t)}
			if err := adapter.Start(session); err != nil {
				t.Fatal(err)
			}
			doc := readJSONDoc(t, hooksPath)
			delete(doc["hooks"].(map[string]interface{}), event)
			if err := writeJSONDocument(hooksPath, doc); err != nil {
				t.Fatal(err)
			}
			if err := adapter.Start(&runnerRemoteSession{WorkspaceDir: session.WorkspaceDir}); err == nil || !strings.Contains(err.Error(), "cursor_approval_hook_missing") {
				t.Fatalf("start accepted missing %s hook: %v", event, err)
			}
			result := adapter.Turn(context.Background(), runnerSessionTurnSpec{HarnessSessionID: session.HarnessSessionID, Dir: session.WorkspaceDir, Text: "must not run"}, func(string, map[string]any) {})
			if result.ErrorCode != "cursor_approval_hook_missing" {
				t.Fatalf("turn accepted missing %s hook: %+v", event, result)
			}
			if lines := readArgvLog(t, logPath); len(lines) != 1 {
				t.Fatalf("Cursor ran without approval hooks: %q", lines)
			}
		})
	}
}
