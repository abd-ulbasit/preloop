package cmd

// `preloop desktop`: brokered live desktop views (#870).

import (
	"errors"
	"fmt"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/spf13/cobra"

	"github.com/preloop/preloop/cli/internal/api"
	"github.com/preloop/preloop/cli/internal/config"
	"github.com/preloop/preloop/cli/internal/version"
)

var (
	desktopEnableAgent     string
	desktopEnableAgentKey  string
	desktopEnableRunnerID  string
	desktopEnableRuntime   string
	desktopEnableDryRun    bool
	desktopServeVNCAddr    string
	desktopServePasswdFile string
	desktopServeDisplay    string
	desktopServeTokenFile  string
	desktopServeAgentID    string
	desktopServeRunnerID   string
)

var desktopCmd = &cobra.Command{
	Use:   "desktop",
	Short: "Share this host's headless desktop as a brokered live view",
	Long: `Brokered live desktop views let an operator watch this host's headless
desktop (Xvfb + x11vnc on 127.0.0.1:5900) in the Preloop console, view only
by default, without opening any inbound port.

'preloop desktop serve' keeps one outbound connection to Preloop and relays
the screen only while a view is open. The VNC password stays on this host.`,
}

var desktopServeCmd = &cobra.Command{
	Use:   "serve",
	Short: "Run the desktop endpoint (outbound tunnel to Preloop)",
	Long: `Run the desktop endpoint for this host.

Authenticates with an agent key or runtime credential, read from
$PRELOOP_DESKTOP_TOKEN or --token-file (default ~/.preloop/desktop/token).
A login session token is refused: the endpoint is a machine, not a person.

The endpoint id is kept in ~/.preloop/desktop.json so the host keeps its
identity across restarts. Normally run by the preloop-desktop-tunnel systemd
user unit that 'preloop desktop enable' installs.`,
	Args: cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		token, err := resolveDesktopToken(home, desktopServeTokenFile)
		if err != nil {
			return err
		}
		baseURL := FlagURL
		if baseURL == "" {
			cfg, err := config.Resolve("", "")
			if err != nil {
				return fmt.Errorf("failed to load config: %w", err)
			}
			baseURL = cfg.APIURL
		}
		passwd := desktopServePasswdFile
		if passwd == "" {
			passwd = filepath.Join(home, ".preloop", "desktop", "vncpasswd")
		}
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt, syscall.SIGTERM)
		defer stop()
		return runDesktopServe(ctx, desktopServeOptions{
			BaseURL:        baseURL,
			Token:          token,
			VNCAddr:        desktopServeVNCAddr,
			PasswdFile:     passwd,
			Display:        desktopServeDisplay,
			StatePath:      filepath.Join(home, ".preloop", "desktop.json"),
			ManagedAgentID: desktopServeAgentID,
			RunnerID:       desktopServeRunnerID,
			Version:        version.Version,
			Out:            cmd.OutOrStdout(),
		})
	},
}

var desktopEnableCmd = &cobra.Command{
	Use:   "enable",
	Short: "Install the headless desktop and enable the desktop tunnel (Debian/Ubuntu)",
	Long: `Install Xvfb, a loopback-only x11vnc and a browser (the same installer as
'preloop agents install-runtime --desktop'), then enable the
preloop-desktop-tunnel systemd user unit that runs 'preloop desktop serve'.

The tunnel authenticates as an agent. With --agent, a credential bound to that
managed agent is created using your login, so the desktop appears on that
agent's page and its sessions. Or pass an existing agent key with --agent-key
(or $PRELOOP_DESKTOP_TOKEN). The key is stored in ~/.preloop/desktop/token
(mode 0600). Re-running is safe.`,
	Example: `  preloop desktop enable --agent 3f2c9a7e-...
  PRELOOP_DESKTOP_TOKEN=agt_... preloop desktop enable`,
	Args: cobra.NoArgs,
	RunE: func(cmd *cobra.Command, args []string) error {
		out := cmd.OutOrStdout()
		if desktopEnableDryRun {
			fmt.Fprint(out, desktopEnableDryRunText()) //nolint:errcheck
			return nil
		}
		baseURL := FlagURL
		var client desktopCredentialClient
		cfg, err := config.Resolve(FlagToken, FlagURL)
		if err == nil && baseURL == "" {
			baseURL = cfg.APIURL
		}
		key := strings.TrimSpace(desktopEnableAgentKey)
		if key == "" {
			key = strings.TrimSpace(os.Getenv(desktopTokenEnv))
		}
		if key == "" && desktopEnableAgent != "" {
			apiClient, err := api.NewClient(FlagToken, FlagURL)
			if err != nil {
				return fmt.Errorf("failed to create API client: %w", err)
			}
			if apiClient.IsAuthenticated() {
				client = apiClient
				baseURL = apiClient.BaseURL()
			}
		}
		hostname, _ := os.Hostname()
		ctx, stop := signal.NotifyContext(cmd.Context(), os.Interrupt)
		defer stop()
		return runDesktopEnable(ctx, desktopEnableOptions{
			Install:  desktopInstallOptions{Runtime: desktopEnableRuntime, Output: out},
			AgentID:  desktopEnableAgent,
			AgentKey: key,
			RunnerID: desktopEnableRunnerID,
			BaseURL:  baseURL,
			Hostname: hostname,
			Client:   client,
		})
	},
}

// resolveDesktopToken prefers $PRELOOP_DESKTOP_TOKEN, then the token file.
func resolveDesktopToken(home, tokenFile string) (string, error) {
	if token := strings.TrimSpace(os.Getenv(desktopTokenEnv)); token != "" {
		return token, nil
	}
	if tokenFile == "" {
		tokenFile = filepath.Join(home, ".preloop", "desktop", "token")
	}
	data, err := os.ReadFile(tokenFile)
	if err != nil {
		if os.IsNotExist(err) {
			return "", errors.New("no desktop credential: set " + desktopTokenEnv +
				" or write an agent key to " + tokenFile)
		}
		return "", err
	}
	return strings.TrimSpace(string(data)), nil
}

func init() {
	flags := desktopServeCmd.Flags()
	flags.StringVar(&desktopServeVNCAddr, "vnc-addr", "127.0.0.1:5900", "loopback address of the local VNC server")
	flags.StringVar(&desktopServePasswdFile, "passwd-file", "", "x11vnc password file (default ~/.preloop/desktop/vncpasswd)")
	flags.StringVar(&desktopServeDisplay, "display", desktopDisplay, "X display being shared")
	flags.StringVar(&desktopServeTokenFile, "token-file", "", "file holding the agent key (default ~/.preloop/desktop/token)")
	flags.StringVar(&desktopServeAgentID, "agent-id", "", "managed agent this desktop belongs to (defaults to the credential's agent)")
	flags.StringVar(&desktopServeRunnerID, "runner-id", "", "runner this desktop belongs to")
	desktopCmd.AddCommand(desktopServeCmd)
	enable := desktopEnableCmd.Flags()
	enable.StringVar(&desktopEnableAgent, "agent", "", "managed agent id this desktop belongs to (creates its desktop credential)")
	enable.StringVar(&desktopEnableAgentKey, "agent-key", "", "existing agent key for the tunnel (default $"+desktopTokenEnv+")")
	enable.StringVar(&desktopEnableRunnerID, "runner-id", "", "runner this desktop belongs to")
	enable.StringVar(&desktopEnableRuntime, "runtime", "", "runtime whose environment gets DISPLAY=:99 (hermes, openclaw, ...)")
	enable.BoolVar(&desktopEnableDryRun, "dry-run", false, "print what would be done")
	desktopCmd.AddCommand(desktopEnableCmd)
	rootCmd.AddCommand(desktopCmd)
}
