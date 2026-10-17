package cmd

import (
	"errors"
	"strings"
	"testing"
)

func TestExtendedHostHarnessIdentity(t *testing.T) {
	for _, tc := range []struct{ agent, exe, harness string }{{"codex", "codex", hostExecHarnessCodex}, {"opencode", "opencode.cmd", hostExecHarnessOpenCode}, {"deepseek", "dsh.exe", hostExecHarnessDeepSeek}, {"claude_code", "claude", hostExecHarnessClaude}} {
		if got := hostExecHarnessForAgentType(tc.agent); got != tc.harness {
			t.Fatalf("%s: %s", tc.agent, got)
		}
		if got := hostExecProfileHarness(hostExecProfile{Executable: tc.exe}); got != tc.harness {
			t.Fatalf("%s: %s", tc.exe, got)
		}
	}
	if got := hostExecProfileHarness(hostExecProfile{Executable: "deepseek"}); got != "" {
		t.Fatal("generic DeepSeek binary must not impersonate dsh")
	}
}

func TestExtendedHostEnvBoundaries(t *testing.T) {
	for _, h := range []string{hostExecHarnessCodex, hostExecHarnessOpenCode, hostExecHarnessDeepSeek} {
		env := hostExecChildEnv("windows", h, hostExecProfile{}, []string{"PATH=C:/bin", "USERPROFILE=C:/Users/test", "PRELOOP_TOKEN=secret", "AWS_SECRET_ACCESS_KEY=secret", "GITHUB_TOKEN=secret", "OPENAI_API_KEY=codex-key", "CODEX_HOME=C:/codex", "DSH_HOME=C:/dsh"})
		joined := strings.Join(env, "\n")
		for _, denied := range []string{"PRELOOP_TOKEN=", "AWS_SECRET_ACCESS_KEY=", "GITHUB_TOKEN="} {
			if strings.Contains(joined, denied) {
				t.Fatalf("%s leaked %s", h, denied)
			}
		}
		if h != hostExecHarnessCodex && strings.Contains(joined, "OPENAI_API_KEY=") {
			t.Fatalf("%s inherited unrelated provider", h)
		}
	}
	got := mergeHostExecManagedEnv([]string{"Path=one", "OPENCODE_CONFIG=old", "OPENCODE_CONFIG_CONTENT=old"}, []string{"OPENCODE_CONFIG=new", "OPENCODE_CONFIG_CONTENT="})
	if strings.Join(got, ";") != "Path=one;OPENCODE_CONFIG=new;OPENCODE_CONFIG_CONTENT=" {
		t.Fatal(got)
	}
}

func TestExtendedHostInventoryRequiresExplicitRunnableProfile(t *testing.T) {
	deps := harnessInventoryDeps{HasEnv: func(string) bool { return false }, HostEnv: func(string) string { return "" }, Resolve: func(string) (string, error) { return "", errors.New("missing") }, Hints: &harnessRuntimeHints{}, Discover: func() []AgentConfig {
		return []AgentConfig{{Name: "Codex CLI", RuntimeState: "installed"}, {Name: "DeepSeek Harness", RuntimeState: "installed"}}
	}}
	inv := buildHarnessInventory(deps)
	for _, entry := range inv.Entries {
		if entry.Harness == hostExecHarnessCodex || entry.Harness == hostExecHarnessDeepSeek {
			if entry.SupportLevel != harnessSupportPresenceOnly || len(entry.Capabilities) != 0 {
				t.Fatalf("unrunnable advertised: %+v", entry)
			}
		}
	}
	deps.Handwritten = []hostExecProfile{{Name: "dsh", Executable: "dsh"}}
	deps.Resolve = func(exe string) (string, error) {
		if exe == "dsh" {
			return "/bin/dsh", nil
		}
		return "", errors.New("missing")
	}
	inv = buildHarnessInventory(deps)
	found := false
	for _, entry := range inv.Entries {
		if entry.Harness == hostExecHarnessDeepSeek {
			found = true
			if entry.SupportLevel != harnessSupportFlowsOnly || entry.GeneratedProfile != nil || entry.SessionMode != "none" {
				t.Fatal(entry)
			}
		}
	}
	if !found {
		t.Fatal("dsh inventory missing")
	}
}

func TestNativeHostScopedTokenRedactedAcrossWrites(t *testing.T) {
	b := &runnerLogBuffer{native: true, harness: hostExecHarnessCodex, secrets: []string{"scoped-test-secret"}}
	_, _ = b.Write([]byte("diagnostic scoped-test-"))
	_, _ = b.Write([]byte("secret details\n"))
	b.finish()
	if logs := strings.Join(b.pending, "\n"); strings.Contains(logs, "scoped-test-secret") || !strings.Contains(logs, "[REDACTED]") {
		t.Fatal("scoped credential reached logs")
	}
}
