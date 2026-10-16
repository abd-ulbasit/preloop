package cmd

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const fakeToken = "ghp_FAKE_TOKEN_VALUE_must_never_leak_1234567890"

type fakeHost struct {
	home        string
	env         map[string]string
	executables map[string]bool
	agents      []AgentConfig
	handwritten []hostExecProfile
	harnesses   map[string]runnerHarnessConfig
	hints       *harnessRuntimeHints
}

func (f fakeHost) deps(goos string) harnessInventoryDeps {
	return harnessInventoryDeps{
		Home: f.home,
		GOOS: goos,
		HasEnv: func(name string) bool {
			_, ok := f.env[name]
			return ok
		},
		HostEnv:  func(name string) string { return f.env[name] },
		Discover: func() []AgentConfig { return f.agents },
		Resolve: func(name string) (string, error) {
			if f.executables[name] {
				return filepath.Join(f.home, "bin", name), nil
			}
			return "", errors.New("not found")
		},
		Version:     func(string) string { return "1.0.95" },
		CursorLogin: func(string) string { return harnessLoginSignedOut },
		Handwritten: f.handwritten,
		Harnesses:   f.harnesses,
		Now:         func() time.Time { return time.Date(2026, 10, 16, 9, 0, 0, 0, time.UTC) },
		Hints:       f.hints,
	}
}

func writeCopilotConfig(t *testing.T, dir, body string) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "config.json"), []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
}

func entryFor(t *testing.T, inv harnessInventory, harness string) harnessInventoryEntry {
	t.Helper()
	for _, entry := range inv.Entries {
		if entry.Harness == harness {
			return entry
		}
	}
	t.Fatalf("no %s entry in %+v", harness, inv.Entries)
	return harnessInventoryEntry{}
}

func TestBuildHarnessInventoryCopilotStoredLoginAcrossOSes(t *testing.T) {
	for _, goos := range []string{"linux", "darwin", "windows"} {
		t.Run(goos, func(t *testing.T) {
			home := t.TempDir()
			writeCopilotConfig(t, filepath.Join(home, ".copilot"), `{
  "model": "claude-sonnet-4.6",
  "last_logged_in_user": {"host": "https://github.com", "login": "jane-doe"},
  "logged_in_users": [{"host": "https://github.com", "login": "jane-doe"}]
}`)
			host := fakeHost{
				home:        home,
				env:         map[string]string{},
				executables: map[string]bool{"copilot": true},
				agents: []AgentConfig{
					{Name: "Claude Desktop", RuntimeState: "present"},
					{Name: "Codex CLI", RuntimeState: "missing"},
				},
			}
			inv := buildHarnessInventory(host.deps(goos))
			copilot := entryFor(t, inv, "copilot_cli")
			if copilot.LoginState != "signed_in" || copilot.LoginSource != "stored" {
				t.Fatalf("login = %s/%s", copilot.LoginState, copilot.LoginSource)
			}
			if copilot.AccountHost != "github.com" || copilot.Version != "1.0.95" {
				t.Fatalf("host/version = %q/%q", copilot.AccountHost, copilot.Version)
			}
			if copilot.SupportLevel != "flows_and_sessions" || copilot.SessionMode != "resume" || copilot.Billing != "seat" {
				t.Fatalf("unexpected copilot entry %+v", copilot)
			}
			if copilot.Governance != "ungoverned" {
				t.Fatalf("governance = %s", copilot.Governance)
			}
			if copilot.GeneratedProfile == nil || *copilot.GeneratedProfile != "copilot" {
				t.Fatalf("generated profile = %v", copilot.GeneratedProfile)
			}
			sources := map[string]string{}
			for _, model := range copilot.Models {
				sources[model.ID] = model.Source
			}
			if sources["auto"] != "static" || sources["claude-sonnet-4.6"] != "static" {
				t.Fatalf("models = %+v", copilot.Models)
			}
			desktop := entryFor(t, inv, "claude_desktop")
			if desktop.SupportLevel != "presence_only" || desktop.LoginState != "not_applicable" {
				t.Fatalf("desktop = %+v", desktop)
			}
			for _, entry := range inv.Entries {
				if entry.Harness == "codex_cli" {
					t.Fatal("a missing runtime must not be reported")
				}
			}
			raw, _ := json.Marshal(inv)
			for _, leak := range []string{"jane-doe", home, "bin"} {
				if strings.Contains(string(raw), leak) {
					t.Fatalf("inventory leaks %q: %s", leak, raw)
				}
			}
			want, _ := harnessInventoryHash(inv.Entries)
			if inv.Hash != want || inv.GeneratedAt != "2026-10-16T09:00:00Z" {
				t.Fatalf("hash/time = %s %s", inv.Hash, inv.GeneratedAt)
			}
		})
	}
}

func TestBuildHarnessInventoryConfiguredModelIsMarkedConfigured(t *testing.T) {
	home := t.TempDir()
	writeCopilotConfig(t, filepath.Join(home, ".copilot"), `{"model": "gpt-5.1-codex"}`)
	host := fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}}
	copilot := entryFor(t, buildHarnessInventory(host.deps("linux")), "copilot_cli")
	last := copilot.Models[len(copilot.Models)-1]
	if last.ID != "gpt-5.1-codex" || last.Source != "configured" {
		t.Fatalf("models = %+v", copilot.Models)
	}
	if copilot.LoginState != "unknown" || copilot.AccountHost != "" {
		t.Fatalf("no login record must stay unknown: %+v", copilot)
	}
}

func TestBuildHarnessInventoryCopilotHomeOverride(t *testing.T) {
	home := t.TempDir()
	custom := filepath.Join(t.TempDir(), "copilot-home")
	writeCopilotConfig(t, custom, `{"logged_in_users": [{"host": "https://acme.ghe.com"}]}`)
	host := fakeHost{home: home, env: map[string]string{"COPILOT_HOME": custom}, executables: map[string]bool{"copilot": true}}
	copilot := entryFor(t, buildHarnessInventory(host.deps("windows")), "copilot_cli")
	if copilot.LoginSource != "stored" || copilot.AccountHost != "acme.ghe.com" {
		t.Fatalf("copilot = %+v", copilot)
	}
}

func TestBuildHarnessInventoryEnvTokenReportsNameOnly(t *testing.T) {
	for _, name := range copilotTokenEnvNames {
		t.Run(name, func(t *testing.T) {
			host := fakeHost{
				home:        t.TempDir(),
				env:         map[string]string{name: fakeToken, "COPILOT_GH_HOST": "https://Acme.GHE.com/"},
				executables: map[string]bool{"copilot": true},
			}
			inv := buildHarnessInventory(host.deps("linux"))
			copilot := entryFor(t, inv, "copilot_cli")
			if copilot.LoginState != "signed_in" || copilot.LoginSource != "env" {
				t.Fatalf("login = %s/%s", copilot.LoginState, copilot.LoginSource)
			}
			if copilot.AccountHost != "acme.ghe.com" {
				t.Fatalf("account_host = %q", copilot.AccountHost)
			}
			raw, _ := json.Marshal(inv)
			if strings.Contains(string(raw), fakeToken) || strings.Contains(string(raw), name) {
				t.Fatalf("token or variable name leaked: %s", raw)
			}
		})
	}
}

func TestBuildHarnessInventoryHostOnlyRejectsCredentialsInURL(t *testing.T) {
	if got := hostOnly("https://user:" + fakeToken + "@github.com"); got != "" {
		t.Fatalf("hostOnly kept a URL with userinfo: %q", got)
	}
	if got := hostOnly("acme.ghe.com:443"); got != "acme.ghe.com" {
		t.Fatalf("hostOnly = %q", got)
	}
}

func TestBuildHarnessInventorySignedOutHintWins(t *testing.T) {
	home := t.TempDir()
	writeCopilotConfig(t, filepath.Join(home, ".copilot"), `{"logged_in_users": [{"host": "https://github.com"}]}`)
	hints := &harnessRuntimeHints{}
	hints.markSignedOut("copilot_cli", true)
	hints.noteModel("copilot_cli", "gpt-5.3")
	hints.noteModel("copilot_cli", "bad model id with spaces")
	host := fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}, hints: hints}
	copilot := entryFor(t, buildHarnessInventory(host.deps("darwin")), "copilot_cli")
	if copilot.LoginState != "signed_out" {
		t.Fatalf("login_state = %s", copilot.LoginState)
	}
	if copilot.Models[0].ID != "auto" || copilot.Models[0].Source != "static" {
		t.Fatalf("models = %+v", copilot.Models)
	}
	if copilot.Models[1].ID != "gpt-5.3" || copilot.Models[1].Source != "observed" {
		t.Fatalf("most recent observed model must follow auto: %+v", copilot.Models)
	}
}

func TestCopilotStaticModelsFor109xIncludeSonnet55(t *testing.T) {
	got := harnessStaticModelsFor("copilot_cli", "1.0.96")
	want := []string{
		"auto",
		"claude-sonnet-5.5",
		"claude-sonnet-4.6",
		"claude-sonnet-4.5",
		"claude-haiku-4.5",
		"gpt-5.2",
		"gpt-5-mini",
	}
	if len(got) != len(want) {
		t.Fatalf("static models = %v", got)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("static models = %v, want %v", got, want)
		}
	}
}

func TestCopilotUsageModelObservedOnceAfterAuto(t *testing.T) {
	saved := runnerHarnessHints
	savedPublisher := runnerInventory
	hints := &harnessRuntimeHints{}
	runnerHarnessHints = hints
	runnerInventory = newRunnerInventoryPublisher(func() harnessInventory { return harnessInventory{} })
	t.Cleanup(func() {
		runnerHarnessHints = saved
		runnerInventory = savedPublisher
	})

	const usage = `{"type":"result","sessionId":"33810b72-1e9a-4a02-bbcb-a125d10b886c","exitCode":0,"usage":{"premiumRequests":1,"model":"gpt-5.4","cwd":"/tmp/secret-workspace","token":"` + fakeToken + `"}}`
	capture := copilotCapture{}
	kind, payload, ok := copilotSessionEvent(&capture, usage)
	if !ok || kind != "usage" {
		t.Fatalf("usage event: ok=%v kind=%s", ok, kind)
	}
	rawPayload, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	for _, leak := range []string{"/tmp/secret-workspace", fakeToken, "cwd", "token"} {
		if strings.Contains(string(rawPayload), leak) {
			t.Fatalf("usage payload leaks %q: %s", leak, rawPayload)
		}
	}
	usageMap, _ := payload["usage"].(map[string]any)
	if usageMap["model"] != "gpt-5.4" || usageMap["premiumRequests"] != float64(1) {
		t.Fatalf("usage = %#v", usageMap)
	}
	for key := range usageMap {
		switch key {
		case "premiumRequests", "model", "totalApiDurationMs", "sessionDurationMs":
		default:
			t.Fatalf("usage key %q is not allowlisted: %#v", key, usageMap)
		}
	}

	result, err := copilotRunnerResult(capture)
	if err != nil {
		t.Fatal(err)
	}
	for key := range result {
		switch key {
		case "status", "harness", "session_id", "model", "premium_requests":
		default:
			t.Fatalf("result key %q is not allowlisted: %#v", key, result)
		}
	}
	if result["model"] != "gpt-5.4" {
		t.Fatalf("result = %#v", result)
	}
	rawResult, _ := json.Marshal(result)
	for _, leak := range []string{"/tmp/secret-workspace", fakeToken} {
		if strings.Contains(string(rawResult), leak) {
			t.Fatalf("host-exec result leaks %q: %s", leak, rawResult)
		}
	}

	capture2 := copilotCapture{}
	applyCopilotLine(&capture2, `{"type":"result","sessionId":"33810b72-1e9a-4a02-bbcb-a125d10b886c","exitCode":0,"usage":{"premiumRequests":1,"model":"claude-sonnet-5.5"}}`)
	if _, err := copilotRunnerResult(capture2); err != nil {
		t.Fatal(err)
	}

	home := t.TempDir()
	host := fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}, hints: hints}
	copilot := entryFor(t, buildHarnessInventory(host.deps("linux")), "copilot_cli")
	counts := map[string]int{}
	for _, model := range copilot.Models {
		counts[model.ID]++
	}
	for _, id := range []string{"auto", "gpt-5.4", "claude-sonnet-5.5", "claude-sonnet-4.6", "claude-sonnet-4.5", "claude-haiku-4.5", "gpt-5.2", "gpt-5-mini"} {
		if counts[id] != 1 {
			t.Fatalf("%s published %d times: %+v", id, counts[id], copilot.Models)
		}
	}
	if copilot.Models[0].ID != "auto" || copilot.Models[0].Source != "static" {
		t.Fatalf("models = %+v", copilot.Models)
	}
	if copilot.Models[1].ID != "claude-sonnet-5.5" || copilot.Models[1].Source != "observed" {
		t.Fatalf("most recent observed model must follow auto: %+v", copilot.Models)
	}
	if copilot.Models[2].ID != "gpt-5.4" || copilot.Models[2].Source != "observed" {
		t.Fatalf("older observed model = %+v", copilot.Models[2])
	}
	raw, _ := json.Marshal(buildHarnessInventory(host.deps("linux")))
	for _, leak := range []string{"/tmp/secret-workspace", fakeToken, "cwd", home, "GITHUB_TOKEN"} {
		if strings.Contains(string(raw), leak) {
			t.Fatalf("inventory leaks %q: %s", leak, raw)
		}
	}
}

func TestBuildHarnessInventoryHandwrittenCopilotProfileSuppressesGenerated(t *testing.T) {
	handwritten, err := normalizeHostExecProfile(hostExecProfile{Name: "copilot-review", Executable: "copilot"})
	if err != nil {
		t.Fatal(err)
	}
	host := fakeHost{
		home:        t.TempDir(),
		env:         map[string]string{},
		executables: map[string]bool{"copilot": true, "cursor-agent": true},
		handwritten: []hostExecProfile{handwritten},
	}
	inv := buildHarnessInventory(host.deps("linux"))
	if entryFor(t, inv, "copilot_cli").GeneratedProfile != nil {
		t.Fatal("hand-written copilot profile must win over the generated one")
	}
	cursor := entryFor(t, inv, "cursor_cli")
	if cursor.GeneratedProfile == nil || *cursor.GeneratedProfile != "cursor" {
		t.Fatalf("cursor generated profile = %v", cursor.GeneratedProfile)
	}
	if cursor.LoginState != "signed_out" || cursor.LoginSource != "cli_status" || cursor.SupportLevel != "flows_and_sessions" {
		t.Fatalf("cursor = %+v", cursor)
	}
	generated := generatedHostExecProfiles(inv)
	if len(generated) != 1 || generated[0].Name != "cursor" || generated[0].Executable != "cursor-agent" {
		t.Fatalf("generated = %+v", generated)
	}
	if generated[0].ModelMap["auto"] != "auto" || generated[0].AllowCheckout || generated[0].ForceWrites {
		t.Fatalf("generated defaults = %+v", generated[0])
	}
}

func TestBuildHarnessInventoryHandwrittenNameCollisionSuppressesGenerated(t *testing.T) {
	handwritten, err := normalizeHostExecProfile(hostExecProfile{Name: "Cursor", Executable: "copilot"})
	if err != nil {
		t.Fatal(err)
	}
	host := fakeHost{home: t.TempDir(), env: map[string]string{}, executables: map[string]bool{"cursor-agent": true}, handwritten: []hostExecProfile{handwritten}}
	if entryFor(t, buildHarnessInventory(host.deps("linux")), "cursor_cli").GeneratedProfile != nil {
		t.Fatal("a hand-written profile named cursor must win")
	}
}

func TestBuildHarnessInventoryInvalidProfileFileGeneratesNothing(t *testing.T) {
	host := fakeHost{home: t.TempDir(), env: map[string]string{}, executables: map[string]bool{"copilot": true}}
	deps := host.deps("linux")
	deps.HandwrittenErr = true
	if entryFor(t, buildHarnessInventory(deps), "copilot_cli").GeneratedProfile != nil {
		t.Fatal("an invalid profile file must not be bypassed by a generated profile")
	}
}

func TestBuildHarnessInventoryDisabledHarnessReportedButNotGenerated(t *testing.T) {
	off := false
	host := fakeHost{
		home:        t.TempDir(),
		env:         map[string]string{},
		executables: map[string]bool{"copilot": true},
		harnesses:   map[string]runnerHarnessConfig{"copilot_cli": {Enabled: &off, SessionsEnabled: true}},
	}
	inv := buildHarnessInventory(host.deps("linux"))
	copilot := entryFor(t, inv, "copilot_cli")
	if copilot.Enabled || copilot.GeneratedProfile != nil {
		t.Fatalf("disabled copilot = %+v", copilot)
	}
	if !copilot.SessionsEnabled {
		t.Fatal("sessions_enabled comes from the host config")
	}
	if len(generatedHostExecProfiles(inv)) != 0 {
		t.Fatal("a disabled harness must not get a generated profile")
	}
}

func TestBuildHarnessInventoryGovernanceFromCopilotHooks(t *testing.T) {
	home := t.TempDir()
	hooks := filepath.Join(home, ".copilot", "hooks")
	if err := os.MkdirAll(hooks, 0o700); err != nil {
		t.Fatal(err)
	}
	write := func(body string) {
		if err := os.WriteFile(filepath.Join(hooks, copilotPreloopHooksFileName), []byte(body), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	host := fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}}
	write(`{"hooks": {"postToolUse": [{"bash": "preloop usage-hook"}]}}`)
	if got := entryFor(t, buildHarnessInventory(host.deps("linux")), "copilot_cli").Governance; got != "partial" {
		t.Fatalf("usage hooks only = %s", got)
	}
	write(`{"hooks": {"preToolUse": [{"bash": "preloop agents permission-hook --client copilot"}]}}`)
	if got := entryFor(t, buildHarnessInventory(host.deps("linux")), "copilot_cli").Governance; got != "governed" {
		t.Fatalf("approval hook = %s", got)
	}
	if got := entryFor(t, buildHarnessInventory(host.deps("windows")), "copilot_cli").Governance; got != "partial" {
		t.Fatalf("a bash hook never runs on Windows, got %s", got)
	}
}

func TestDiscoveredHarnessMapping(t *testing.T) {
	host := fakeHost{
		home: t.TempDir(),
		env:  map[string]string{},
		agents: []AgentConfig{
			{Name: "Claude Code", RuntimeState: "present", AuthState: "ready", IsOnboarded: true, OnboardingState: "fully_onboarded"},
			{Name: "OpenCode", RuntimeState: "unknown", AuthState: "not_logged_in", IsOnboarded: true, OnboardingState: "mcp_proxy_only"},
			{Name: "Windsurf", RuntimeState: "present"},
		},
	}
	inv := buildHarnessInventory(host.deps("linux"))
	if len(inv.Entries) != 2 {
		t.Fatalf("entries = %+v", inv.Entries)
	}
	claude := entryFor(t, inv, "claude_code")
	// No claude executable on PATH: presence only, never a runner path.
	if claude.Governance != "governed" || claude.Billing != "metered" || claude.SupportLevel != "presence_only" || claude.LoginState != "signed_in" || claude.GeneratedProfile != nil {
		t.Fatalf("claude = %+v", claude)
	}
	opencode := entryFor(t, inv, "opencode")
	if opencode.Governance != "partial" || opencode.LoginState != "signed_out" {
		t.Fatalf("opencode = %+v", opencode)
	}
}

func TestCursorLoginFromStatus(t *testing.T) {
	cases := map[string]string{
		"Not logged in":                   "signed_out",
		"✓ Logged in as jane@example.com": "signed_in",
		"something else":                  "unknown",
	}
	for output, want := range cases {
		if got := cursorLoginFromStatus(output, true); got != want {
			t.Fatalf("%q -> %s, want %s", output, got, want)
		}
	}
	if parseHarnessVersion("GitHub Copilot CLI 1.0.95.\n") != "1.0.95" {
		t.Fatal("version parse")
	}
}

func TestCopilotLoginStatusFrames(t *testing.T) {
	for _, tc := range []struct{ name, payload, want string }{
		{"signed in", `{"jsonrpc":"2.0","id":1,"result":{"isAuthenticated":true,"login":"jane-doe","token":"must-never-publish"}}`, harnessLoginSignedIn},
		{"signed out", `{"jsonrpc":"2.0","id":1,"result":{"isAuthenticated":false}}`, harnessLoginSignedOut},
		{"missing verdict", `{"jsonrpc":"2.0","id":1,"result":{}}`, harnessLoginUnknown},
		{"wrong response", `{"jsonrpc":"2.0","id":2,"result":{"isAuthenticated":true}}`, harnessLoginUnknown},
		{"unsupported method", `{"jsonrpc":"2.0","id":1,"error":{"code":-32601}}`, harnessLoginUnknown},
		{"malformed", `{`, harnessLoginUnknown},
	} {
		t.Run(tc.name, func(t *testing.T) {
			frame := fmt.Sprintf("Content-Length: %d\r\n\r\n%s", len(tc.payload), tc.payload)
			if got := readCopilotLoginStatus(strings.NewReader(frame)); got != tc.want {
				t.Fatalf("got %s, want %s", got, tc.want)
			}
		})
	}
	for _, frame := range []string{"", "Content-Length: 65537\r\n\r\n", "Content-Length: -1\r\n\r\n", "Content-Length: nope\r\n\r\n", "Content-Length: 100\r\n\r\n{}", "Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}", strings.Repeat("x", 4097)} {
		if got := readCopilotLoginStatus(strings.NewReader(frame)); got != harnessLoginUnknown {
			t.Fatalf("invalid frame accepted: %s", got)
		}
	}
}

func TestBuildHarnessInventoryCopilotSDKLogin(t *testing.T) {
	for _, tc := range []struct{ name, stored, verdict, want, source string }{
		{"modern credential store", "// Settings moved to settings.json\n{\"firstLaunchAt\":\"2026-01-01\"}", harnessLoginSignedIn, harnessLoginSignedIn, "cli_status"},
		{"signed out overrides stale record", `{"logged_in_users":[{"host":"https://github.com"}]}`, harnessLoginSignedOut, harnessLoginSignedOut, "cli_status"},
		{"old CLI fallback", `{"logged_in_users":[{"host":"https://github.com"}]}`, harnessLoginUnknown, harnessLoginSignedIn, "stored"},
		{"no login unknown", `{}`, harnessLoginUnknown, harnessLoginUnknown, "unknown"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			home := t.TempDir()
			writeCopilotConfig(t, filepath.Join(home, ".copilot"), tc.stored)
			deps := (fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}}).deps("darwin")
			deps.CopilotLogin = func(string) string { return tc.verdict }
			entry := entryFor(t, buildHarnessInventory(deps), hostExecHarnessCopilot)
			if entry.LoginState != tc.want || entry.LoginSource != tc.source {
				t.Fatalf("login = %s/%s, want %s/%s", entry.LoginState, entry.LoginSource, tc.want, tc.source)
			}
		})
	}
}

func TestProbeCopilotLoginSDKProcess(t *testing.T) {
	skipNoShebangOnWindows(t, "fake Copilot SDK transport")
	dir := t.TempDir()
	executable := filepath.Join(dir, "copilot")
	payload := `{"jsonrpc":"2.0","id":1,"result":{"isAuthenticated":true,"login":"jane-doe"}}`
	script := fmt.Sprintf("#!/bin/sh\nprintf 'Content-Length: %%s\\r\\n\\r\\n%%s' '%d' '%s'\nwhile :; do :; done\n", len(payload), payload)
	if err := os.WriteFile(executable, []byte(script), 0700); err != nil {
		t.Fatal(err)
	}
	started := time.Now()
	if got := probeCopilotLogin(executable); got != harnessLoginSignedIn {
		t.Fatalf("got %s", got)
	}
	if elapsed := time.Since(started); elapsed > 3*time.Second {
		t.Fatalf("status cleanup took %s", elapsed)
	}
	if err := os.WriteFile(executable, []byte("#!/bin/sh\nexit 1\n"), 0700); err != nil {
		t.Fatal(err)
	}
	if got := probeCopilotLogin(executable); got != harnessLoginUnknown {
		t.Fatalf("failed command got %s", got)
	}
}

func TestCopilotModernStoredConfig(t *testing.T) {
	for _, tc := range []struct{ name, config, verdict, want, host string }{
		{"commented modern identity", "// Copilot configuration\n" + `{"model":"custom-model","authTokens":{},"lastLoggedInUser":{"host":"https://example.ghe.com","login":"example"},"loggedInUsers":[{"host":"https://example.ghe.com"}]}`, harnessLoginSignedIn, harnessLoginSignedIn, "example.ghe.com"},
		{"identity only unknown", `{"lastLoggedInUser":{"host":"https://example.ghe.com"}}`, harnessLoginUnknown, harnessLoginUnknown, ""},
		{"token presence unknown", `{"authTokens":{"https://example.ghe.com:example":{"token":"synthetic-token"}},"loggedInUsers":[{"host":"https://example.ghe.com"}]}`, harnessLoginUnknown, harnessLoginUnknown, ""},
		{"signed out overrides credentials", `{"authTokens":{"https://example.ghe.com:example":{"token":"synthetic-token"}},"loggedInUsers":[{"host":"https://example.ghe.com"}]}`, harnessLoginSignedOut, harnessLoginSignedOut, ""},
		{"modern overrides stale legacy identity", `{"logged_in_users":[{"host":"https://old.example.com"}],"loggedInUsers":[]}`, harnessLoginUnknown, harnessLoginUnknown, ""},
		{"legacy JSONC fallback", "// legacy configuration\n" + `{"logged_in_users":[{"host":"https://example.ghe.com"}]}`, harnessLoginUnknown, harnessLoginSignedIn, "example.ghe.com"},
		{"malformed config fails closed", `{"logged_in_users":[{"host":"https://example.ghe.com"}],`, harnessLoginUnknown, harnessLoginUnknown, ""},
		{"malformed identity fails closed", `{"lastLoggedInUser":"invalid"}`, harnessLoginUnknown, harnessLoginUnknown, ""},
	} {
		t.Run(tc.name, func(t *testing.T) {
			home := t.TempDir()
			writeCopilotConfig(t, filepath.Join(home, ".copilot"), tc.config)
			deps := (fakeHost{home: home, env: map[string]string{}, executables: map[string]bool{"copilot": true}}).deps("windows")
			deps.CopilotLogin = func(string) string { return tc.verdict }
			entry := entryFor(t, buildHarnessInventory(deps), hostExecHarnessCopilot)
			if entry.LoginState != tc.want || entry.AccountHost != tc.host {
				t.Fatalf("state/host = %s/%q, want %s/%q", entry.LoginState, entry.AccountHost, tc.want, tc.host)
			}
			if tc.name == "commented modern identity" {
				found := false
				for _, model := range entry.Models {
					found = found || model.ID == "custom-model"
				}
				if !found {
					t.Fatal("configured model missing from inventory")
				}
			}
			encoded, err := json.Marshal(entry)
			if err != nil || strings.Contains(string(encoded), "synthetic-token") || strings.Contains(string(encoded), "authTokens") {
				t.Fatal("inventory must not contain stored credentials")
			}
		})
	}
}

func TestReadCopilotStoredConfigRejectsOversizedDocument(t *testing.T) {
	dir := t.TempDir()
	writeCopilotConfig(t, dir, `{"logged_in_users":[{"host":"https://github.com"}]}`+strings.Repeat(" ", 256*1024))
	if cfg := readCopilotStoredConfig(dir); cfg.legacyLogin || cfg.LastLoggedInUser != nil || len(cfg.LoggedInUsers) != 0 {
		t.Fatal("oversized config must not establish login")
	}
}
