package cmd

import (
	"context"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

func TestClaudeSessionArgsGolden(t *testing.T) {
	profile := hostExecProfile{Name: "claude", Executable: "claude", ModelMap: map[string]string{"opus-5.5": "claude-opus-5-5"}}
	turn1, err := claudeSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: strings.ToUpper(claudeTestSessionID), Text: "-p explain", Model: "opus-5.5"})
	if err != nil {
		t.Fatal(err)
	}
	want1 := []string{"-p", "--output-format", "stream-json", "--verbose", "--model", "claude-opus-5-5", "--session-id", claudeTestSessionID}
	if !reflect.DeepEqual(turn1, want1) {
		t.Fatalf("turn 1 argv\n got %q\nwant %q", turn1, want1)
	}
	turnN, err := claudeSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: claudeTestSessionID, Text: "next", Model: "auto", Resume: true})
	if err != nil {
		t.Fatal(err)
	}
	wantN := []string{"-p", "--output-format", "stream-json", "--verbose", "--resume", claudeTestSessionID}
	if !reflect.DeepEqual(turnN, wantN) {
		t.Fatalf("turn N argv\n got %q\nwant %q", turnN, wantN)
	}
	// An unmapped plain model id passes through; a flag-shaped one does not.
	if args, err := claudeSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: claudeTestSessionID, Text: "x", Model: "sonnet"}); err != nil || cursorArgValue(args, "--model") != "sonnet" {
		t.Fatalf("plain model: %q %v", args, err)
	}
	if _, err := claudeSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: claudeTestSessionID, Text: "x", Model: "--dangerously-skip-permissions"}); err == nil {
		t.Fatal("a flag-shaped model must be refused")
	}
	if _, err := claudeSessionArgs(profile, runnerSessionTurnSpec{HarnessSessionID: "../../other", Text: "x"}); err == nil {
		t.Fatal("a non-uuid session id must be refused")
	}
}

func TestClaudeSessionStartRequiresApprovalHook(t *testing.T) {
	hook := false
	a := &claudeSessionAdapter{
		loadProfile:           func() (hostExecProfile, error) { return hostExecProfile{Name: "claude", Executable: "claude"}, nil },
		approvalHookInstalled: func() bool { return hook },
		resolveCommand:        func(string) (string, []string, error) { return "/bin/claude", nil, nil },
		environ:               os.Environ,
		newID:                 newSessionUUID,
	}
	if err := a.Start(&runnerRemoteSession{}); err == nil || !strings.Contains(err.Error(), "claude_approval_hook_missing") {
		t.Fatalf("want claude_approval_hook_missing, got %v", err)
	}
	hook = true
	s := &runnerRemoteSession{}
	if err := a.Start(s); err != nil || !uuidRe.MatchString(s.HarnessSessionID) {
		t.Fatalf("start: %v %q", err, s.HarnessSessionID)
	}
	a.loadProfile = func() (hostExecProfile, error) {
		return hostExecProfile{Name: "claude", Executable: "claude", Argv: []string{"--dangerously-skip-permissions"}}, nil
	}
	if err := a.Start(&runnerRemoteSession{}); err == nil {
		t.Fatal("a profile with argv must be refused")
	}
}

func TestClaudeSessionEventMapping(t *testing.T) {
	capture := claudeCapture{}
	var kinds []string
	stream := claudeSuccessStream + "\n" +
		`{"type":"assistant","message":{"model":"claude-opus-5-5","content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}}` + "\n" +
		`{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":"a"}]}}` + "\n" +
		"Invalid API key · Please run /login"
	for _, line := range strings.Split(stream, "\n") {
		for _, event := range claudeSessionEvents(&capture, line) {
			kinds = append(kinds, event.kind)
		}
	}
	want := []string{"agent_message", "usage", "tool_call", "tool_result", "stderr"}
	if !reflect.DeepEqual(kinds, want) {
		t.Fatalf("kinds = %v, want %v", kinds, want)
	}
	if capture.SessionID != claudeTestSessionID || capture.Model != "claude-opus-5-5" {
		t.Fatalf("capture = %+v", capture)
	}
}

// installFakeClaudeSession writes a fake claude that logs its working
// directory, argv and stdin, then prints a successful stream-json turn for
// the session it was asked to start or resume. A prompt containing "sleep"
// blocks so a stop can kill it mid-turn.
func installFakeClaudeSession(t *testing.T) string {
	t.Helper()
	argvLog := filepath.Join(t.TempDir(), "argv.log")
	body := `prompt="$(cat)"
printf '%s|%s|%s\n' "$(pwd -P)" "$*" "$prompt" >> '` + argvLog + `'
case "$prompt" in *sleep*) sleep 30 ;; esac
sid=""
prev=""
for a in "$@"; do case "$prev" in --session-id|--resume) sid="$a" ;; esac; prev="$a"; done
echo "{\"type\":\"system\",\"subtype\":\"init\",\"session_id\":\"$sid\",\"model\":\"claude-opus-5-5\"}"
echo "{\"type\":\"assistant\",\"message\":{\"model\":\"claude-opus-5-5\",\"content\":[{\"type\":\"text\",\"text\":\"ok\"},{\"type\":\"tool_use\",\"id\":\"t1\",\"name\":\"Bash\",\"input\":{}}]},\"session_id\":\"$sid\"}"
echo "{\"type\":\"result\",\"subtype\":\"success\",\"is_error\":false,\"num_turns\":1,\"session_id\":\"$sid\",\"usage\":{\"input_tokens\":7,\"output_tokens\":2}}"`
	setupClaudeHost(t, true, body)
	return argvLog
}

func enableClaudeTestSessions(t *testing.T, dir string) {
	t.Helper()
	writeRunnerConfig(t, map[string]any{
		"harnesses":              map[string]any{"claude_code": map[string]any{"sessions_enabled": true}},
		"authorized_directories": []any{map[string]any{"id": "dir_9f2c", "path": dir, "label": "ims", "mode": "write"}},
	})
}

func TestClaudeSessionEndToEndWithFakeBinary(t *testing.T) {
	argvLog := installFakeClaudeSession(t)
	dir := realTempDir(t)
	enableClaudeTestSessions(t, dir)
	m := newRunnerSessionManager(nil)
	m.notifyHost = func(string) {}
	h := &sessionHarness{t: t, m: m}

	start := startMsg(sessTestID)
	start.Harness = "claude_code"
	start.Model = "opus"
	start.FirstPrompt = "first turn"
	m.handle(start)
	msgs := h.waitFor(isTurnDone(runnerSessionFirstTurnID))
	done := findMsg(msgs, isTurnDone(runnerSessionFirstTurnID))
	if done["status"] != "ok" {
		t.Fatalf("turn 1 failed: %v", msgs)
	}
	if findMsg(msgs, func(m map[string]any) bool { return m["kind"] == "tool_call" }) == nil {
		t.Fatalf("tool call event missing: %v", msgs)
	}
	m.handle(runnerWSMessage{Type: "session_turn", RemoteSessionID: sessTestID, TurnID: "t2", Text: "second turn"})
	h.waitFor(isTurnDone("t2"))

	lines := readArgvLog(t, argvLog)
	if len(lines) != 2 {
		t.Fatalf("want 2 claude runs, got %q", lines)
	}
	sid := m.sessions[sessTestID].HarnessSessionID
	if !strings.HasPrefix(lines[0], dir+"|") || !strings.Contains(lines[0], "--session-id "+sid) || strings.Contains(lines[0], "--resume") || !strings.Contains(lines[0], "--model opus") || !strings.HasSuffix(lines[0], "|first turn") {
		t.Fatalf("turn 1: %q", lines[0])
	}
	if !strings.Contains(lines[1], "--resume "+sid) || strings.Contains(lines[1], "--session-id") || !strings.HasSuffix(lines[1], "|second turn") {
		t.Fatalf("turn 2 must resume %s: %q", sid, lines[1])
	}

	m.handle(runnerWSMessage{Type: "session_turn", RemoteSessionID: sessTestID, TurnID: "t3", Text: "please sleep"})
	h.waitFor(isState("running"))
	began := time.Now()
	m.handle(runnerWSMessage{Type: "session_stop", RemoteSessionID: sessTestID, Mode: "kill"})
	msgs = h.waitFor(isState("ended"))
	if time.Since(began) > 10*time.Second {
		t.Fatalf("kill took %s", time.Since(began))
	}
	if done := findMsg(msgs, isTurnDone("t3")); done == nil || done["error_code"] != "killed" {
		t.Fatalf("killed turn: %v", msgs)
	}
}

func TestClaudeSessionTurnReportsUsage(t *testing.T) {
	installFakeClaudeSession(t)
	a := newClaudeSessionAdapter()
	result := a.Turn(context.Background(), runnerSessionTurnSpec{HarnessSessionID: claudeTestSessionID, Text: "hi", Dir: realTempDir(t)}, func(string, map[string]any) {})
	if result.Status != "ok" || !result.SessionCreated {
		t.Fatalf("result = %+v", result)
	}
	if result.Usage["input_tokens"] != int64(7) || result.Usage["output_tokens"] != int64(2) || result.Usage["model"] != "claude-opus-5-5" {
		t.Fatalf("usage = %v", result.Usage)
	}
}

func TestClaudeSessionTurnFailsWhenNotLoggedIn(t *testing.T) {
	setupClaudeHost(t, true, "cat >/dev/null\necho 'Invalid API key · Please run /login'\nexit 1")
	a := newClaudeSessionAdapter()
	result := a.Turn(context.Background(), runnerSessionTurnSpec{HarnessSessionID: claudeTestSessionID, Text: "hi", Dir: realTempDir(t)}, func(string, map[string]any) {})
	if result.Status != "error" || result.ErrorCode != rejectHarnessSignedOut {
		t.Fatalf("want harness_signed_out, got %+v", result)
	}
}

func TestClaudeSessionTurnRefusesMismatchedSession(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null
cat <<'EOF'
`+claudeSuccessStream+`
EOF`)
	a := newClaudeSessionAdapter()
	other := "00000000-1111-4222-8333-444444444444"
	result := a.Turn(context.Background(), runnerSessionTurnSpec{HarnessSessionID: other, Text: "hi", Dir: realTempDir(t), Resume: true}, func(string, map[string]any) {})
	if result.Status != "error" || !strings.Contains(result.Error, "different session") {
		t.Fatalf("result = %+v", result)
	}
}

func TestRunnerSessionHarnessArgAcceptsClaude(t *testing.T) {
	for _, arg := range []string{"claude", "claude_code", " Claude_Code "} {
		if got, err := runnerSessionHarnessArg(arg); err != nil || got != hostExecHarnessClaude {
			t.Fatalf("%q -> %q, %v", arg, got, err)
		}
	}
}
