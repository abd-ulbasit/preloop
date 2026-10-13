package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/testenv"
)

const claudeTestExecID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
const claudeTestSessionID = "5e0c9a8e-2f7b-4d43-9a51-0d4f1c2b3a4e"

// Lines shaped like Claude Code 2.1.296 `-p --output-format stream-json
// --verbose`, trimmed to the fields the runner reads.
const claudeSuccessStream = `{"type":"system","subtype":"init","cwd":"/w","session_id":"` + claudeTestSessionID + `","tools":["Bash"],"model":"claude-opus-5-5","permissionMode":"default"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ok"}]},"session_id":"` + claudeTestSessionID + `"}
{"type":"result","subtype":"success","is_error":false,"duration_ms":2345,"num_turns":1,"result":"ok","session_id":"` + claudeTestSessionID + `","total_cost_usd":0.01,"usage":{"input_tokens":3,"output_tokens":5}}`

const claudeHookSettings = `{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"/usr/local/bin/preloop agents permission-hook --source claude_code","timeout":610}]}]}}`

// setupClaudeHost isolates HOME, CLAUDE_CONFIG_DIR and the managed settings
// path, optionally installs the approval hook, and puts a fake claude on PATH.
func setupClaudeHost(t *testing.T, hook bool, body string) string {
	t.Helper()
	skipNoShebangOnWindows(t, "claude host execution fake CLI")
	testenv.SetTempHome(t)
	configDir := t.TempDir()
	t.Setenv("CLAUDE_CONFIG_DIR", configDir)
	managed := filepath.Join(t.TempDir(), "managed-settings.json")
	previous := claudeManagedSettingsPath
	claudeManagedSettingsPath = func() string { return managed }
	t.Cleanup(func() { claudeManagedSettingsPath = previous })
	if hook {
		if err := os.WriteFile(filepath.Join(configDir, "settings.json"), []byte(claudeHookSettings), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	dir := t.TempDir()
	script := "#!/bin/sh\n" + body + "\n"
	if err := os.WriteFile(filepath.Join(dir, "claude"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
	writeHostExecProfiles(t, []hostExecProfile{{
		Name:          "claude-seat",
		Executable:    "claude",
		WorkspaceRoot: t.TempDir(),
		ModelMap:      map[string]string{"opus": "opus"},
	}})
	return configDir
}

func claudeJob(extra map[string]any) map[string]any {
	job := map[string]any{
		"agent_type":          "claude_code",
		"completion_protocol": "host_exec",
		"host_exec_profile":   "claude-seat",
		"execution_id":        claudeTestExecID,
		"prompt":              "--help is not a flag here",
	}
	for key, value := range extra {
		job[key] = value
	}
	return job
}

func runClaudeHostJob(t *testing.T, job map[string]any) (leasedJobOutcome, *runnerLogBuffer) {
	t.Helper()
	cmd, _, timeout, err := newHostExecJobCmd(job)
	if err != nil {
		t.Fatal(err)
	}
	buffer := &runnerLogBuffer{native: true, harness: hostExecHarnessClaude}
	cmd.Stdout, cmd.Stderr = buffer, buffer
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	outcome := waitHostExecJob(cmd, claudeTestExecID, buffer, nil, timeout, "claude-seat")
	if outcome.status == "FAILED" {
		outcome.errMsg = claudeHostExecFailure(buffer, outcome.errMsg)
	}
	return outcome, buffer
}

func TestClaudeHarnessForAgentType(t *testing.T) {
	if got := hostExecHarnessForAgentType("claude_code"); got != hostExecHarnessClaude {
		t.Fatalf("harness = %q", got)
	}
	if got := hostExecProfileHarness(hostExecProfile{Executable: "/opt/homebrew/bin/claude"}); got != hostExecHarnessClaude {
		t.Fatalf("profile harness = %q", got)
	}
}

func TestNormalizeClaudeHostExecProfile(t *testing.T) {
	base := hostExecProfile{Name: "claude-seat", Executable: "claude", ModelMap: map[string]string{"opus": "opus"}}
	if _, err := normalizeHostExecProfile(base); err != nil {
		t.Fatalf("plain claude profile rejected: %v", err)
	}
	cases := map[string]hostExecProfile{
		"argv":            func() hostExecProfile { p := base; p.Argv = []string{"--dangerously-skip-permissions"}; return p }(),
		"settings":        func() hostExecProfile { p := base; p.Argv = []string{"--settings", "{}"}; return p }(),
		"force_writes":    func() hostExecProfile { p := base; p.ForceWrites = true; return p }(),
		"allow_tools":     func() hostExecProfile { p := base; p.AllowTools = []string{"Bash"}; return p }(),
		"allow_all_tools": func() hostExecProfile { p := base; p.AllowAllTools = true; return p }(),
	}
	for name, profile := range cases {
		if _, err := normalizeHostExecProfile(profile); err == nil {
			t.Errorf("%s: expected rejection", name)
		}
	}
}

func TestBuildClaudeHostExecArgs(t *testing.T) {
	profile := hostExecProfile{Name: "claude-seat", Executable: "claude", ModelMap: map[string]string{"opus-5.5": "claude-opus-5-5"}}
	job := map[string]any{"prompt": "-p do it", "model_identifier": "opus-5.5"}
	args, err := buildClaudeHostExecArgs(profile, job, "/w/.preloop/claude-mcp-config.json", "")
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"-p", "--output-format", "stream-json", "--verbose",
		"--model", "claude-opus-5-5",
		"--mcp-config", "/w/.preloop/claude-mcp-config.json", "--allowedTools", "mcp__preloop-flow",
	}
	if !reflect.DeepEqual(args, want) {
		t.Fatalf("args = %q", args)
	}
	for _, arg := range args {
		if strings.Contains(arg, "do it") {
			t.Fatal("the prompt must travel on stdin, not argv")
		}
	}
	if _, err := buildClaudeHostExecArgs(profile, map[string]any{"prompt": "x", "model_identifier": "gpt-5"}, "", ""); err == nil {
		t.Fatal("a model outside model_map must be refused")
	}
	if _, err := buildClaudeHostExecArgs(profile, map[string]any{"prompt": "  "}, "", ""); err == nil {
		t.Fatal("an empty prompt must be refused")
	}
	if _, err := buildClaudeHostExecArgs(profile, map[string]any{"prompt": "x"}, "", "--settings"); err == nil {
		t.Fatal("a non-UUID resume id must be refused")
	}
	resumed, err := buildClaudeHostExecArgs(profile, map[string]any{"prompt": "x"}, "", strings.ToUpper(claudeTestSessionID))
	if err != nil || resumed[len(resumed)-1] != claudeTestSessionID || resumed[len(resumed)-2] != "--resume" {
		t.Fatalf("resume args = %q, err = %v", resumed, err)
	}
}

func TestClaudeStreamResult(t *testing.T) {
	capture := claudeCapture{}
	for _, line := range strings.Split(claudeSuccessStream, "\n") {
		applyClaudeLine(&capture, line)
	}
	result, err := claudeRunnerResult(capture)
	if err != nil {
		t.Fatal(err)
	}
	if result["status"] != "success" || result["harness"] != "claude_code" || result["session_id"] != claudeTestSessionID || result["model"] != "claude-opus-5-5" {
		t.Fatalf("result = %#v", result)
	}
	usage, _ := result["usage"].(map[string]any)
	if usage["input_tokens"] != int64(3) || usage["output_tokens"] != int64(5) || result["num_turns"] != 1 {
		t.Fatalf("usage = %#v", result)
	}

	failed := claudeCapture{}
	applyClaudeLine(&failed, `{"type":"result","subtype":"error_max_turns","is_error":true,"session_id":"`+claudeTestSessionID+`"}`)
	if result, err := claudeRunnerResult(failed); err != nil || result["status"] != "failure" {
		t.Fatalf("failure result = %#v, %v", result, err)
	}

	none := claudeCapture{}
	applyClaudeLine(&none, "Invalid API key · Please run /login")
	if _, err := claudeRunnerResult(none); err == nil {
		t.Fatal("a run without a result event must not succeed")
	}
	if none.ErrorLine == "" {
		t.Fatal("plain-text startup error not kept")
	}

	twice := claudeCapture{}
	applyClaudeLine(&twice, `{"type":"result","subtype":"success","is_error":false}`)
	applyClaudeLine(&twice, `{"type":"result","subtype":"success","is_error":false}`)
	if _, err := claudeRunnerResult(twice); err == nil {
		t.Fatal("two result events must not succeed")
	}

	hostile := claudeCapture{}
	applyClaudeLine(&hostile, `{"type":"system","subtype":"init","session_id":"../../x","model":"bad model; rm"}`)
	if hostile.SessionID != "" || hostile.Model != "" {
		t.Fatalf("unvalidated identifiers kept: %#v", hostile)
	}
}

func TestClaudeLoginFromStatus(t *testing.T) {
	cases := map[string]string{
		`{"loggedIn":true,"authMethod":"claude.ai","email":"jane@example.com"}`: harnessLoginSignedIn,
		`{"loggedIn":false}`:    harnessLoginSignedOut,
		`{"authMethod":"none"}`: harnessLoginUnknown,
		`Not logged in`:         harnessLoginUnknown,
	}
	for output, want := range cases {
		if got := claudeLoginFromStatus(output); got != want {
			t.Errorf("%s: got %s, want %s", output, got, want)
		}
	}
}

func TestClaudeHooksHaveApproval(t *testing.T) {
	var doc map[string]interface{}
	if err := json.Unmarshal([]byte(claudeHookSettings), &doc); err != nil {
		t.Fatal(err)
	}
	if !claudeHooksHaveApproval(doc) {
		t.Fatal("onboarded hook not recognised")
	}
	for _, body := range []string{
		`{}`,
		`{"hooks":{"PreToolUse":[{"matcher":"Bash","hooks":[{"type":"command","command":"preloop agents permission-hook --source claude_code"}]}]}}`,
		`{"hooks":{"PreToolUse":[{"matcher":"*","hooks":[{"type":"command","command":"preloop agents permission-hook --source codex_cli"}]}]}}`,
		`{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"preloop agents permission-hook --source claude_code"}]}]}}`,
	} {
		var doc map[string]interface{}
		if err := json.Unmarshal([]byte(body), &doc); err != nil {
			t.Fatal(err)
		}
		if claudeHooksHaveApproval(doc) {
			t.Errorf("%s: must not count as the approval hook", body)
		}
	}
}

func TestClaudeHostExecEnvKeepsLoginOnly(t *testing.T) {
	environ := []string{
		"HOME=/home/u", "PATH=/usr/bin", "ANTHROPIC_API_KEY=k", "CLAUDE_CONFIG_DIR=/c",
		"CLAUDE_CODE_USE_BEDROCK=1", "PRELOOP_TOKEN=secret", "AWS_SECRET_ACCESS_KEY=x", "COPILOT_GITHUB_TOKEN=y",
	}
	got := strings.Join(hostExecChildEnv("linux", hostExecHarnessClaude, hostExecProfile{}, environ), "\n")
	for _, keep := range []string{"ANTHROPIC_API_KEY=k", "CLAUDE_CONFIG_DIR=/c", "CLAUDE_CODE_USE_BEDROCK=1", "HOME=/home/u"} {
		if !strings.Contains(got, keep) {
			t.Errorf("missing %s", keep)
		}
	}
	for _, drop := range []string{"PRELOOP_TOKEN", "AWS_SECRET_ACCESS_KEY", "COPILOT_GITHUB_TOKEN"} {
		if strings.Contains(got, drop) {
			t.Errorf("leaked %s", drop)
		}
	}
}

func TestClaudeHostJobRunsWithPromptOnStdin(t *testing.T) {
	argsFile := filepath.Join(t.TempDir(), "args")
	stdinFile := filepath.Join(t.TempDir(), "stdin")
	envFile := filepath.Join(t.TempDir(), "env")
	t.Setenv("PRELOOP_TOKEN", "runner-secret")
	setupClaudeHost(t, true, `printf '%s\n' "$@" > '`+argsFile+`'
cat > '`+stdinFile+`'
env > '`+envFile+`'
cat <<'EOF'
`+claudeSuccessStream+`
EOF`)
	outcome, _ := runClaudeHostJob(t, claudeJob(map[string]any{"model_identifier": "opus"}))
	if outcome.status != "SUCCEEDED" || outcome.result["harness"] != "claude_code" || outcome.result["session_id"] != claudeTestSessionID {
		t.Fatalf("outcome = %+v", outcome)
	}
	args, _ := os.ReadFile(argsFile)
	if got := strings.Fields(string(args)); !reflect.DeepEqual(got, []string{"-p", "--output-format", "stream-json", "--verbose", "--model", "opus"}) {
		t.Fatalf("argv = %q", got)
	}
	stdin, _ := os.ReadFile(stdinFile)
	if string(stdin) != "--help is not a flag here" {
		t.Fatalf("stdin = %q", stdin)
	}
	env, _ := os.ReadFile(envFile)
	if strings.Contains(string(env), "runner-secret") {
		t.Fatal("runner credential reached Claude Code")
	}
	if !strings.Contains(string(env), "PRELOOP_FLOW_EXECUTION_ID="+claudeTestExecID) {
		t.Fatal("flow execution id not exported for the usage hooks")
	}
}

func TestClaudeHostJobWritesMCPConfigOutsideProjectConfig(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null`)
	useHostFlowTestURL(t)
	run, err := newHostExecJob(claudeJob(map[string]any{"host_exec_mcp": map[string]any{"token": hostFlowTestToken}}))
	if err != nil {
		t.Fatal(err)
	}
	defer run.cleanup()
	args := run.cmd.Args[1:]
	configPath := cursorArgValue(args, "--mcp-config")
	if configPath != filepath.Join(run.workspace, ".preloop", "claude-mcp-config.json") {
		t.Fatalf("mcp config = %q in %q", configPath, args)
	}
	if cursorArgValue(args, "--allowedTools") != "mcp__"+hostExecMCPServerName {
		t.Fatalf("flow MCP server not granted: %q", args)
	}
	for _, arg := range args {
		if strings.Contains(arg, hostFlowTestToken) {
			t.Fatalf("token in argv: %q", args)
		}
	}
	info, err := os.Stat(configPath)
	if err != nil || info.Mode().Perm() != 0o600 {
		t.Fatalf("config mode: %v %v", info, err)
	}
	raw, _ := os.ReadFile(configPath)
	var doc struct {
		MCPServers map[string]struct {
			Type    string            `json:"type"`
			URL     string            `json:"url"`
			Headers map[string]string `json:"headers"`
		} `json:"mcpServers"`
	}
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	server := doc.MCPServers[hostExecMCPServerName]
	if server.Type != "http" || !strings.HasSuffix(server.URL, "/mcp/v1") || server.Headers["Authorization"] != "Bearer "+hostFlowTestToken {
		t.Fatalf("server = %+v", server)
	}
	for _, name := range []string{".mcp.json", filepath.Join(".claude", "settings.json")} {
		if _, err := os.Stat(filepath.Join(run.workspace, name)); err == nil {
			t.Fatalf("wrote project config %s", name)
		}
	}
}

func TestClaudeHostJobRequiresApprovalHook(t *testing.T) {
	setupClaudeHost(t, false, `echo should-not-run`)
	if _, _, _, err := newHostExecJobCmd(claudeJob(nil)); err == nil || !strings.Contains(err.Error(), "claude_approval_hook_missing") {
		t.Fatalf("err = %v", err)
	}
}

func TestClaudeHostJobAcceptsManagedApprovalHook(t *testing.T) {
	setupClaudeHost(t, false, `cat >/dev/null`)
	if err := os.WriteFile(claudeManagedSettingsPath(), []byte(claudeHookSettings), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := newHostExecJobCmd(claudeJob(nil)); err != nil {
		t.Fatalf("managed hook rejected: %v", err)
	}
}

func TestClaudeHostJobNotLoggedIn(t *testing.T) {
	runnerHarnessHints.markSignedOut(hostExecHarnessClaude, false)
	t.Cleanup(func() { runnerHarnessHints.markSignedOut(hostExecHarnessClaude, false) })
	setupClaudeHost(t, true, `cat >/dev/null
echo 'Invalid API key · Please run /login'
exit 1`)
	outcome, _ := runClaudeHostJob(t, claudeJob(nil))
	if outcome.status != "FAILED" || !strings.HasPrefix(outcome.errMsg, "claude_not_logged_in") {
		t.Fatalf("outcome = %+v", outcome)
	}
	if signedOut, _ := runnerHarnessHints.snapshot(hostExecHarnessClaude); !signedOut {
		t.Fatal("signed-out hint not recorded")
	}
}

func TestClaudeHostJobRejectsUnmappedModel(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null`)
	if _, _, _, err := newHostExecJobCmd(claudeJob(map[string]any{"model_identifier": "gpt-5"})); err == nil {
		t.Fatal("expected model_map refusal")
	}
}

func claudeInventoryHost(t *testing.T, login string, env map[string]string, hook bool) harnessInventoryDeps {
	t.Helper()
	host := fakeHost{home: t.TempDir(), env: env, executables: map[string]bool{"claude": true}}
	deps := host.deps("darwin")
	deps.Version = func(string) string { return parseHarnessVersion("2.1.296 (Claude Code)") }
	deps.ClaudeLogin = func(string) string { return login }
	deps.ClaudeHook = func() bool { return hook }
	deps.Hints = &harnessRuntimeHints{}
	return deps
}

func TestBuildHarnessInventoryClaudeVerified(t *testing.T) {
	inv := buildHarnessInventory(claudeInventoryHost(t, harnessLoginSignedIn, map[string]string{}, true))
	entry := entryFor(t, inv, "claude_code")
	if entry.Version != "2.1.296" || entry.SupportLevel != harnessSupportFlowsOnly || entry.LoginSource != "cli_status" || entry.Governance != "governed" || entry.Billing != "seat" {
		t.Fatalf("entry = %+v", entry)
	}
	if entry.GeneratedProfile == nil || *entry.GeneratedProfile != "claude" {
		t.Fatalf("generated profile = %v", entry.GeneratedProfile)
	}
	if !reflect.DeepEqual(entry.Capabilities, []string{"host_exec", "claude_code", "stdout", "cancel"}) {
		t.Fatalf("capabilities = %v", entry.Capabilities)
	}
	ids := []string{}
	for _, model := range entry.Models {
		ids = append(ids, model.ID)
	}
	if !reflect.DeepEqual(ids, []string{"opus", "sonnet", "haiku"}) {
		t.Fatalf("models = %v", ids)
	}
	profiles := generatedHostExecProfiles(inv)
	if len(profiles) != 1 || profiles[0].Name != "claude" || profiles[0].Executable != "claude" || profiles[0].ModelMap["opus"] != "opus" {
		t.Fatalf("generated profiles = %+v", profiles)
	}
}

func TestBuildHarnessInventoryClaudeEnvLogin(t *testing.T) {
	entry := entryFor(t, buildHarnessInventory(claudeInventoryHost(t, harnessLoginUnknown, map[string]string{"ANTHROPIC_API_KEY": fakeToken}, false)), "claude_code")
	if entry.LoginState != harnessLoginSignedIn || entry.LoginSource != "env" || entry.SupportLevel != harnessSupportFlowsOnly || entry.Governance != "ungoverned" {
		t.Fatalf("entry = %+v", entry)
	}
}

func TestBuildHarnessInventoryClaudeUnverifiedIsPresenceOnly(t *testing.T) {
	for _, login := range []string{harnessLoginSignedOut, harnessLoginUnknown} {
		entry := entryFor(t, buildHarnessInventory(claudeInventoryHost(t, login, map[string]string{}, true)), "claude_code")
		if entry.SupportLevel != harnessSupportPresenceOnly || entry.GeneratedProfile != nil || len(entry.Capabilities) != 0 {
			t.Fatalf("%s: entry = %+v", login, entry)
		}
	}
	deps := claudeInventoryHost(t, harnessLoginSignedIn, map[string]string{}, true)
	deps.Hints.markSignedOut(hostExecHarnessClaude, true)
	entry := entryFor(t, buildHarnessInventory(deps), "claude_code")
	if entry.SupportLevel != harnessSupportPresenceOnly || entry.LoginState != harnessLoginSignedOut {
		t.Fatalf("signed-out hint: entry = %+v", entry)
	}
}
