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

	"github.com/preloop/preloop/cli/internal/config"
	"github.com/preloop/preloop/cli/internal/version"
)

var (
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
	rootCmd.AddCommand(desktopCmd)
}
