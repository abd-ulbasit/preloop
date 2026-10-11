package cmd

// `preloop desktop enable` and the tunnel unit behind brokered desktop views
// (#870). Installs the loopback desktop (same installer as
// `agents install-runtime --desktop`), stores an agent credential for the
// endpoint, and enables preloop-desktop-tunnel.service, which runs
// `preloop desktop serve`.

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/preloop/preloop/cli/internal/api"
)

const desktopTunnelService = "preloop-desktop-tunnel.service"

// desktopCredentialClient is the slice of the API client used to mint the
// endpoint's agent credential.
type desktopCredentialClient interface {
	Post(path string, body, result interface{}) error
}

type desktopTunnelOptions struct {
	Install    desktopInstallOptions
	BaseURL    string
	Token      string
	RunnerID   string
	Executable string
}

func desktopTokenPath(home string) string {
	return filepath.Join(home, ".preloop", "desktop", "token")
}

// mintDesktopCredential creates a durable credential bound to one managed
// agent, so `desktop serve` registers the endpoint against that agent.
func mintDesktopCredential(client desktopCredentialClient, agentID, hostname string, now time.Time) (string, error) {
	if strings.TrimSpace(agentID) == "" {
		return "", errors.New("agent id is required")
	}
	host := slugifyAgentName(hostname)
	if host == "" {
		host = "host"
	}
	request := managedAgentCredentialCreateRequest{
		Name:          fmt.Sprintf("desktop-%s-%s", host, now.UTC().Format("20060102150405")),
		Description:   "Desktop endpoint credential (preloop desktop serve)",
		ExpiresInDays: 365,
		Scopes:        []string{"mcp:read"},
	}
	var response managedAgentCredentialCreateResponse
	if err := client.Post("/api/v1/agents/"+agentID+"/credentials", request, &response); err != nil {
		return "", fmt.Errorf("failed to create the desktop credential: %w", err)
	}
	if strings.TrimSpace(response.Token) == "" {
		return "", errors.New("the API returned no desktop credential")
	}
	return response.Token, nil
}

func renderDesktopTunnelUnit(executable, baseURL, runnerID string) string {
	exec := strconv.Quote(executable) + " desktop serve --url " + strconv.Quote(baseURL)
	if runnerID != "" {
		exec += " --runner-id " + strconv.Quote(runnerID)
	}
	return `[Unit]
Description=Preloop desktop tunnel (outbound only, for brokered live views)
After=network-online.target ` + desktopService + `
Wants=network-online.target

[Service]
Type=simple
ExecStart=` + exec + `
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
`
}

// enableDesktopTunnel stores the credential and starts the tunnel unit. The
// desktop itself must already be installed.
func enableDesktopTunnel(ctx context.Context, opts desktopTunnelOptions) error {
	home, err := opts.Install.homeDir()
	if err != nil {
		return err
	}
	out := opts.Install.Output
	if out == nil {
		out = os.Stdout
	}
	if strings.TrimSpace(opts.BaseURL) == "" {
		return errors.New("the Preloop URL is unknown; pass --url")
	}
	tokenPath := desktopTokenPath(home)
	if token := strings.TrimSpace(opts.Token); token != "" {
		if strings.Contains(token, ".") {
			return errors.New("the desktop tunnel needs an agent key, not a login session token")
		}
		if err := writeDesktopFile(tokenPath, token+"\n", 0o600); err != nil {
			return err
		}
	} else if _, err := os.Stat(tokenPath); err != nil {
		return errors.New("no desktop credential: pass --agent <id> (mints one) or --agent-key")
	}
	executable := opts.Executable
	if executable == "" {
		if executable, err = os.Executable(); err != nil {
			return err
		}
	}
	unitPath := filepath.Join(home, ".config", "systemd", "user", desktopTunnelService)
	if err := writeDesktopFile(unitPath, renderDesktopTunnelUnit(executable, opts.BaseURL, opts.RunnerID), 0o644); err != nil {
		return err
	}
	steps := [][]string{
		{"--user", "daemon-reload"},
		{"--user", "enable", "--now", desktopTunnelService},
		{"--user", "restart", desktopTunnelService},
	}
	for i, args := range steps {
		if _, err := opts.Install.run(ctx, "systemctl", args...); err != nil {
			if i == len(steps)-1 {
				return fmt.Errorf("failed to restart %s: %w", desktopTunnelService, err)
			}
			fmt.Fprintln(out, "systemd user session unavailable; started the desktop tunnel with nohup") //nolint:errcheck
			logPath := filepath.Join(home, ".preloop", "desktop", "tunnel.log")
			command := "nohup " + shellSingleQuote(executable) + " desktop serve --url " +
				shellSingleQuote(opts.BaseURL)
			if opts.RunnerID != "" {
				command += " --runner-id " + shellSingleQuote(opts.RunnerID)
			}
			command += " >>" + shellSingleQuote(logPath) + " 2>&1 &"
			if _, err := opts.Install.run(ctx, "sh", "-c", command); err != nil {
				return fmt.Errorf("failed to start the desktop tunnel with nohup: %w", err)
			}
			break
		}
	}
	fmt.Fprintf(out, "Enabled %s: this desktop can be opened as a live view in Preloop (view only by default)\n", desktopTunnelService) //nolint:errcheck
	return nil
}

func desktopEnableDryRunText() string {
	return desktopDryRunText() + strings.Join([]string{
		"Would enable the desktop tunnel:",
		"  create a desktop credential for the agent (or use --agent-key), stored in ~/.preloop/desktop/token (0600)",
		"  write ~/.config/systemd/user/" + desktopTunnelService + " running 'preloop desktop serve'",
		"  systemctl --user enable --now " + desktopTunnelService + " (outbound connection only, no inbound port)",
		"",
	}, "\n")
}

type desktopEnableOptions struct {
	Install    desktopInstallOptions
	AgentID    string
	AgentKey   string
	RunnerID   string
	BaseURL    string
	Hostname   string
	Executable string
	Client     desktopCredentialClient
	Now        func() time.Time
}

// runDesktopEnable installs the desktop, resolves the endpoint credential and
// enables the tunnel. Credential precedence: --agent-key / $PRELOOP_DESKTOP_TOKEN,
// then a credential minted for --agent, then an existing token file.
func runDesktopEnable(ctx context.Context, opts desktopEnableOptions) error {
	if err := installDesktop(ctx, opts.Install); err != nil {
		return err
	}
	token := strings.TrimSpace(opts.AgentKey)
	if token == "" && strings.TrimSpace(opts.AgentID) != "" {
		if opts.Client == nil {
			return errors.New("not authenticated - run 'preloop login' first, or pass --agent-key")
		}
		now := time.Now
		if opts.Now != nil {
			now = opts.Now
		}
		minted, err := mintDesktopCredential(opts.Client, opts.AgentID, opts.Hostname, now())
		if err != nil {
			return err
		}
		token = minted
	}
	return enableDesktopTunnel(ctx, desktopTunnelOptions{
		Install:    opts.Install,
		BaseURL:    opts.BaseURL,
		Token:      token,
		RunnerID:   opts.RunnerID,
		Executable: opts.Executable,
	})
}

// runtimeDesktopTunnelTarget finds the API client, base URL and managed agent
// of a freshly onboarded runtime. Tests replace it.
var runtimeDesktopTunnelTarget = func(spec runtimeInstallSpec) (desktopCredentialClient, string, string, error) {
	discovered, err := discoverAgents(io.Discard, false)
	if err != nil {
		return nil, "", "", err
	}
	agent, err := findDiscoveredAgent(discovered, spec.onboardAgentName)
	if err != nil {
		return nil, "", "", err
	}
	client, err := api.NewClient(FlagToken, FlagURL)
	if err != nil {
		return nil, "", "", err
	}
	if !client.IsAuthenticated() {
		return nil, "", "", errors.New("not authenticated")
	}
	managed, err := getManagedAgentForDiscovered(client, agent)
	if err != nil {
		return nil, "", "", err
	}
	return client, client.BaseURL(), managed.ID, nil
}

// enableRuntimeDesktopTunnel links the installed desktop to the onboarded
// runtime's managed agent and starts the tunnel.
func enableRuntimeDesktopTunnel(ctx context.Context, spec runtimeInstallSpec, install desktopInstallOptions) error {
	client, baseURL, agentID, err := runtimeDesktopTunnelTarget(spec)
	if err != nil {
		return fmt.Errorf("desktop tunnel: %w (run 'preloop desktop enable --agent <id>' once the agent is onboarded)", err)
	}
	hostname, _ := os.Hostname()
	token, err := mintDesktopCredential(client, agentID, hostname, time.Now())
	if err != nil {
		return err
	}
	return enableDesktopTunnel(ctx, desktopTunnelOptions{Install: install, BaseURL: baseURL, Token: token})
}
