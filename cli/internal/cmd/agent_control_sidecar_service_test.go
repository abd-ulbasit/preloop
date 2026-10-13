package cmd

import (
	"bytes"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

func TestSidecarLaunchdCarriesPathAndLogs(t *testing.T) {
	env := agentControlSidecarServiceEnv{
		Path:    "/opt/homebrew/bin:/usr/bin",
		LogPath: "/Users/example/.preloop/logs/claude-sidecar.log",
		Home:    "/Users/example",
	}
	for _, spec := range []agentControlSidecarSpec{claudeAgentControlSidecarSpec(), codexAgentControlSidecarSpec()} {
		body := renderAgentControlSidecarLaunchd(spec, "/usr/local/bin/preloop", env)
		for _, want := range []string{
			"<key>EnvironmentVariables</key>\n  <dict><key>HOME</key><string>/Users/example</string><key>PATH</key><string>/opt/homebrew/bin:/usr/bin</string></dict>",
			"<key>StandardOutPath</key><string>/Users/example/.preloop/logs/claude-sidecar.log</string>",
			"<key>StandardErrorPath</key><string>/Users/example/.preloop/logs/claude-sidecar.log</string>",
			"<string>" + spec.Command + "</string><string>sidecar</string><string>run</string>",
			"<key>KeepAlive</key><true/>",
		} {
			if !strings.Contains(body, want) {
				t.Fatalf("%s launchd missing %q:\n%s", spec.Command, want, body)
			}
		}
	}
}

func TestSidecarLaunchdEscapesPath(t *testing.T) {
	body := renderAgentControlSidecarLaunchd(
		claudeAgentControlSidecarSpec(),
		"/usr/local/bin/preloop",
		agentControlSidecarServiceEnv{Path: "/a&b/bin"},
	)
	if !strings.Contains(body, "<string>/a&amp;b/bin</string>") {
		t.Fatalf("PATH not escaped:\n%s", body)
	}
}

func TestSidecarSystemdCarriesPathAndLogs(t *testing.T) {
	body := renderAgentControlSidecarSystemd(
		claudeAgentControlSidecarSpec(),
		"/usr/local/bin/preloop",
		agentControlSidecarServiceEnv{
			Path:    "/home/example/.nvm/versions/node/v22/bin:/usr/bin",
			LogPath: "/home/example/.preloop/logs/claude-sidecar.log",
			Home:    "/home/example",
		},
	)
	for _, want := range []string{
		`Environment="HOME=/home/example"`,
		`Environment="PATH=/home/example/.nvm/versions/node/v22/bin:/usr/bin"`,
		"StandardOutput=append:/home/example/.preloop/logs/claude-sidecar.log",
		"StandardError=append:/home/example/.preloop/logs/claude-sidecar.log",
		`ExecStart="/usr/local/bin/preloop" claude sidecar run`,
	} {
		if !strings.Contains(body, want) {
			t.Fatalf("systemd missing %q:\n%s", want, body)
		}
	}
}

func TestSidecarSystemdSkipsLogPathWithSpaces(t *testing.T) {
	body := renderAgentControlSidecarSystemd(
		claudeAgentControlSidecarSpec(),
		"/usr/local/bin/preloop",
		agentControlSidecarServiceEnv{LogPath: "/home/a b/.preloop/logs/x.log"},
	)
	if strings.Contains(body, "StandardOutput=") {
		t.Fatalf("unsafe log path written:\n%s", body)
	}
}

func TestSidecarServicePathPutsNodeFirstAndDedupes(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("service files are written on macOS and Linux only")
	}
	binDir := t.TempDir()
	node := filepath.Join(binDir, "node")
	if err := os.WriteFile(node, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", binDir)
	// Pin HOME so the plugin fallback locations under ~ cannot match.
	t.Setenv("HOME", t.TempDir())
	t.Setenv("NPM_CONFIG_PREFIX", t.TempDir())
	got := agentControlSidecarServicePath(
		claudeAgentControlSidecarSpec(),
		"/usr/bin"+string(os.PathListSeparator)+binDir+string(os.PathListSeparator)+"relative",
	)
	parts := filepath.SplitList(got)
	if len(parts) == 0 || parts[0] != binDir {
		t.Fatalf("node dir not first: %q", got)
	}
	count := 0
	for _, part := range parts {
		if part == binDir {
			count++
		}
		if part == "relative" {
			t.Fatalf("relative dir kept: %q", got)
		}
	}
	if count != 1 {
		t.Fatalf("node dir repeated: %q", got)
	}
	if !strings.Contains(got, "/usr/bin") {
		t.Fatalf("shell PATH dropped: %q", got)
	}
}

func TestSidecarServiceExitStatusParsing(t *testing.T) {
	spec := claudeAgentControlSidecarSpec()
	original := agentControlSidecarServiceManagerOutput
	t.Cleanup(func() { agentControlSidecarServiceManagerOutput = original })

	agentControlSidecarServiceManagerOutput = func(agentControlSidecarSpec) (string, error) {
		return "{\n\t\"LimitLoadToSessionType\" = \"Aqua\";\n\t\"Label\" = \"ai.preloop.claude-sidecar\";\n\t\"LastExitStatus\" = 256;\n};\n", nil
	}
	if got := agentControlSidecarServiceExitStatus(spec); got != "last exit status: 1" {
		t.Fatalf("launchd status = %q", got)
	}
	for raw, want := range map[string]string{
		"0":   "0",
		"9":   "terminated by signal 9",
		"11":  "terminated by signal 11",
		"512": "2",
		"-1":  "-1",
		"x":   "x",
	} {
		if got := launchdExitStatusText(raw); got != want {
			t.Fatalf("launchdExitStatusText(%q) = %q, want %q", raw, got, want)
		}
	}
	agentControlSidecarServiceManagerOutput = func(agentControlSidecarSpec) (string, error) {
		return "ExecMainStatus=1\nActiveState=failed\n", nil
	}
	if got := agentControlSidecarServiceExitStatus(spec); got != "last exit status: 1" {
		t.Fatalf("systemd status = %q", got)
	}
}

func TestSidecarStatusPrintsExitStatusWhenSocketDown(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("unix sockets")
	}
	t.Setenv("HOME", t.TempDir())
	original := agentControlSidecarServiceManagerOutput
	t.Cleanup(func() { agentControlSidecarServiceManagerOutput = original })
	agentControlSidecarServiceManagerOutput = func(agentControlSidecarSpec) (string, error) {
		return "\t\"LastExitStatus\" = 256;\n", nil
	}
	var out bytes.Buffer
	cmd := &cobra.Command{}
	cmd.SetOut(&out)
	if err := runAgentControlSidecarStatus(claudeAgentControlSidecarSpec(), cmd, nil); err != nil {
		t.Fatal(err)
	}
	text := out.String()
	if !strings.Contains(text, "socket: down") ||
		!strings.Contains(text, "last exit status: 1") ||
		!strings.Contains(text, filepath.Join(".preloop", "logs", "claude-sidecar.log")) {
		t.Fatalf("status output = %q", text)
	}
}

func TestSystemdQuoteEscapesSpecialCharacters(t *testing.T) {
	cases := map[string]string{
		"PATH=/usr/bin":      `"PATH=/usr/bin"`,
		`PATH=/a%b`:          `"PATH=/a%%b"`,
		`PATH=/a"b`:          `"PATH=/a\"b"`,
		`PATH=/a\b`:          `"PATH=/a\\b"`,
		"PATH=/a\nb":         `"PATH=/a b"`,
		`HOME=/home/x y%"\z`: `"HOME=/home/x y%%\"\\z"`,
	}
	for in, want := range cases {
		if got := systemdQuote(in); got != want {
			t.Fatalf("systemdQuote(%q) = %s, want %s", in, got, want)
		}
	}
}
