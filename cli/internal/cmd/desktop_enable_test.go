package cmd

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type fakeCredentialClient struct {
	paths  []string
	bodies []managedAgentCredentialCreateRequest
	token  string
	err    error
}

func (f *fakeCredentialClient) Post(path string, body, result interface{}) error {
	f.paths = append(f.paths, path)
	f.bodies = append(f.bodies, body.(managedAgentCredentialCreateRequest))
	if f.err != nil {
		return f.err
	}
	resp := result.(*managedAgentCredentialCreateResponse)
	resp.Token = f.token
	return nil
}

func enableTestOptions(t *testing.T, calls *[]string, run func(name string, args []string) ([]byte, error)) desktopEnableOptions {
	t.Helper()
	install := desktopTestOptions(t, calls, run)
	return desktopEnableOptions{
		Install:    install,
		BaseURL:    "https://preloop.example.com",
		Hostname:   "Host.Example.com",
		Executable: "/home/example/.local/bin/preloop",
		Now:        func() time.Time { return time.Date(2026, 10, 11, 12, 0, 0, 0, time.UTC) },
	}
}

func TestDesktopEnableMintsAgentCredentialAndEnablesTunnel(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, nil)
	client := &fakeCredentialClient{token: "agt_desktop_secret"}
	opts.AgentID = "agent-123"
	opts.Client = client
	if err := runDesktopEnable(context.Background(), opts); err != nil {
		t.Fatal(err)
	}
	home := opts.Install.HomeDir
	if len(client.paths) != 1 || client.paths[0] != "/api/v1/agents/agent-123/credentials" {
		t.Fatalf("credential calls = %v", client.paths)
	}
	if !strings.HasPrefix(client.bodies[0].Name, "desktop-") {
		t.Fatalf("credential name = %q", client.bodies[0].Name)
	}
	token, err := os.ReadFile(desktopTokenPath(home))
	if err != nil || string(token) != "agt_desktop_secret\n" {
		t.Fatalf("token file = %q %v", token, err)
	}
	info, _ := os.Stat(desktopTokenPath(home))
	if info.Mode().Perm() != 0o600 {
		t.Fatalf("token mode = %v", info.Mode())
	}
	unit, err := os.ReadFile(filepath.Join(home, ".config", "systemd", "user", desktopTunnelService))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(unit), `ExecStart="/home/example/.local/bin/preloop" desktop serve --url "https://preloop.example.com"`) {
		t.Fatalf("unit:\n%s", unit)
	}
	if strings.Contains(string(unit), "agt_desktop_secret") {
		t.Fatal("unit file must not contain the credential")
	}
	joined := strings.Join(calls, "\n")
	for _, want := range []string{
		"systemctl --user enable --now " + desktopService,
		"systemctl --user enable --now " + desktopTunnelService,
	} {
		if !strings.Contains(joined, want) {
			t.Fatalf("missing %q in:\n%s", want, joined)
		}
	}
	if strings.Index(joined, desktopService) > strings.Index(joined, desktopTunnelService) {
		t.Fatal("the tunnel must be enabled after the desktop is installed")
	}
}

func TestDesktopEnableRejectsUnsupportedDistroBeforeMinting(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, nil)
	opts.Install.OSReleasePath = writeOSRelease(t, "ID=fedora\n")
	client := &fakeCredentialClient{token: "agt_x"}
	opts.AgentID = "agent-123"
	opts.Client = client
	err := runDesktopEnable(context.Background(), opts)
	if err == nil || err.Error() != "desktop_unsupported_distro" {
		t.Fatalf("err = %v", err)
	}
	if len(client.paths) != 0 || len(calls) != 0 {
		t.Fatalf("ran %v / minted %v on an unsupported distro", calls, client.paths)
	}
}

func TestDesktopEnableWithAgentKeySkipsMinting(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, nil)
	opts.AgentKey = "agt_given"
	opts.AgentID = "agent-123"
	client := &fakeCredentialClient{token: "agt_minted"}
	opts.Client = client
	if err := runDesktopEnable(context.Background(), opts); err != nil {
		t.Fatal(err)
	}
	if len(client.paths) != 0 {
		t.Fatal("minted despite --agent-key")
	}
	token, _ := os.ReadFile(desktopTokenPath(opts.Install.HomeDir))
	if string(token) != "agt_given\n" {
		t.Fatalf("token = %q", token)
	}
}

func TestDesktopEnableCredentialErrors(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, nil)
	if err := runDesktopEnable(context.Background(), opts); err == nil || !strings.Contains(err.Error(), "no desktop credential") {
		t.Fatalf("no credential: %v", err)
	}
	opts = enableTestOptions(t, &calls, nil)
	opts.AgentKey = "header.payload.signature"
	if err := runDesktopEnable(context.Background(), opts); err == nil || !strings.Contains(err.Error(), "not a login session") {
		t.Fatalf("jwt: %v", err)
	}
	opts = enableTestOptions(t, &calls, nil)
	opts.AgentID = "agent-123"
	if err := runDesktopEnable(context.Background(), opts); err == nil || !strings.Contains(err.Error(), "not authenticated") {
		t.Fatalf("no client: %v", err)
	}
	opts = enableTestOptions(t, &calls, nil)
	opts.AgentID = "agent-123"
	opts.Client = &fakeCredentialClient{err: errors.New("403 forbidden")}
	if err := runDesktopEnable(context.Background(), opts); err == nil || !strings.Contains(err.Error(), "403") {
		t.Fatalf("mint failure: %v", err)
	}
}

func TestDesktopEnableKeepsExistingTokenOnRerun(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, nil)
	opts.AgentKey = "agt_first"
	if err := runDesktopEnable(context.Background(), opts); err != nil {
		t.Fatal(err)
	}
	opts.AgentKey = ""
	if err := runDesktopEnable(context.Background(), opts); err != nil {
		t.Fatalf("rerun: %v", err)
	}
	token, _ := os.ReadFile(desktopTokenPath(opts.Install.HomeDir))
	if string(token) != "agt_first\n" {
		t.Fatalf("token = %q", token)
	}
}

func TestDesktopTunnelFallsBackToNohup(t *testing.T) {
	var calls []string
	opts := enableTestOptions(t, &calls, func(name string, args []string) ([]byte, error) {
		if name == "systemctl" {
			return nil, errors.New("no user bus")
		}
		return nil, nil
	})
	opts.AgentKey = "agt_x"
	opts.RunnerID = "runner-9"
	if err := runDesktopEnable(context.Background(), opts); err != nil {
		t.Fatal(err)
	}
	var nohup string
	for _, call := range calls {
		if strings.HasPrefix(call, "sh -c nohup '/home/example/.local/bin/preloop' desktop serve") {
			nohup = call
		}
	}
	if nohup == "" || !strings.Contains(nohup, "--runner-id 'runner-9'") || strings.Contains(nohup, "agt_x") {
		t.Fatalf("nohup call = %q in %v", nohup, calls)
	}
}

func TestEnableRuntimeDesktopTunnelLinksOnboardedAgent(t *testing.T) {
	var calls []string
	install := desktopTestOptions(t, &calls, nil)
	client := &fakeCredentialClient{token: "agt_runtime"}
	original := runtimeDesktopTunnelTarget
	runtimeDesktopTunnelTarget = func(spec runtimeInstallSpec) (desktopCredentialClient, string, string, error) {
		return client, "https://preloop.example.com", "agent-777", nil
	}
	t.Cleanup(func() { runtimeDesktopTunnelTarget = original })
	if err := enableRuntimeDesktopTunnel(context.Background(), runtimeInstallSpec{kind: "hermes"}, install); err != nil {
		t.Fatal(err)
	}
	if len(client.paths) != 1 || client.paths[0] != "/api/v1/agents/agent-777/credentials" {
		t.Fatalf("paths = %v", client.paths)
	}
	if !strings.Contains(strings.Join(calls, "\n"), "enable --now "+desktopTunnelService) {
		t.Fatalf("tunnel not enabled: %v", calls)
	}

	runtimeDesktopTunnelTarget = func(spec runtimeInstallSpec) (desktopCredentialClient, string, string, error) {
		return nil, "", "", errors.New("not authenticated")
	}
	err := enableRuntimeDesktopTunnel(context.Background(), runtimeInstallSpec{kind: "hermes"}, install)
	if err == nil || !strings.Contains(err.Error(), "preloop desktop enable --agent") {
		t.Fatalf("err = %v", err)
	}
}

func TestDesktopEnableDryRunMentionsTunnel(t *testing.T) {
	text := desktopEnableDryRunText()
	for _, want := range []string{desktopTunnelService, "no inbound port", "0600", "-localhost"} {
		if !strings.Contains(text, want) {
			t.Fatalf("dry run missing %q:\n%s", want, text)
		}
	}
}
