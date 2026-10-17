package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"github.com/preloop/preloop/cli/internal/api"
	"github.com/preloop/preloop/cli/internal/config"
	"github.com/preloop/preloop/cli/internal/testenv"
	"github.com/spf13/cobra"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestChooseRunnerConsent(t *testing.T) {
	for _, mode := range []string{"", "always", "ask_me", "never", "invalid"} {
		t.Run(mode, func(t *testing.T) {
			testenv.SetTempHome(t)
			var out bytes.Buffer
			err := chooseRunnerConsent(strings.NewReader(mode+"\n"), &out, true, "")
			if mode == "invalid" {
				if err == nil {
					t.Fatal("accepted invalid mode")
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			want := mode
			if want == "" {
				want = "ask_me"
			}
			got, _, err := config.RunnerFlowExecutionConsent()
			if err != nil || got != want {
				t.Fatalf("choice = %q, error %v", got, err)
			}
			if !strings.Contains(out.String(), "1) Ask me each time (default)") {
				t.Fatalf("missing default: %s", &out)
			}
		})
	}
}

func TestChooseRunnerConsentByNumber(t *testing.T) {
	for answer, want := range map[string]string{"1": "ask_me", "2": "always", "3": "never"} {
		testenv.SetTempHome(t)
		var out bytes.Buffer
		if err := chooseRunnerConsent(strings.NewReader(answer+"\n"), &out, true, ""); err != nil {
			t.Fatal(err)
		}
		got, _, err := config.RunnerFlowExecutionConsent()
		if err != nil || got != want {
			t.Fatalf("answer %s: choice = %q, error %v", answer, got, err)
		}
	}
}

func TestChooseRunnerConsentUnattendedAndManaged(t *testing.T) {
	testenv.SetTempHome(t)
	var out bytes.Buffer
	if err := chooseRunnerConsent(strings.NewReader(""), &out, false, ""); err == nil {
		t.Fatal("unattended install accepted no choice")
	}
	if err := chooseRunnerConsent(strings.NewReader(""), &out, false, "never"); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(t.TempDir(), "managed.yaml")
	if err := os.WriteFile(path, []byte("runner:\n  flow_execution_consent: ask_me\n"), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PRELOOP_MANAGED_CONFIG", path)
	if err := chooseRunnerConsent(strings.NewReader(""), &out, false, ""); err != nil {
		t.Fatal(err)
	}
	if err := chooseRunnerConsent(strings.NewReader(""), &out, false, "always"); err == nil {
		t.Fatal("flag overrode managed consent")
	}
}

func TestRegisterRunnerConsentDoesNotOverwriteConsoleChoice(t *testing.T) {
	testenv.SetTempHome(t)
	oldOnce, oldOverride := runnerOnce, runnerConsentOverride
	runnerOnce, runnerConsentOverride = nil, ""
	t.Cleanup(func() { runnerOnce, runnerConsentOverride = oldOnce, oldOverride })
	if err := config.SetRunnerFlowExecutionConsent("never"); err != nil {
		t.Fatal(err)
	}
	var bodies []map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/v1/runners/register" {
			http.NotFound(w, r)
			return
		}
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Error(err)
		}
		bodies = append(bodies, body)
		_ = json.NewEncoder(w).Encode(map[string]any{"id": "runner-example", "token": "example-token", "name": "Example"})
	}))
	defer server.Close()
	client, err := api.NewClient("example-token", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	for range 2 {
		if _, err := loadOrRegisterRunner(client, "Example", "example-host", []string{}, 1); err != nil {
			t.Fatal(err)
		}
	}
	if len(bodies) != 2 {
		t.Fatalf("registration count %d", len(bodies))
	}
	if bodies[0]["flow_execution_consent"] != "never" {
		t.Fatalf("missing initial consent: %v", bodies[0])
	}
	if _, ok := bodies[1]["flow_execution_consent"]; ok {
		t.Fatal("restart overwrote server consent")
	}
	runnerConsentOverride = "never"
	if _, err := loadOrRegisterRunner(client, "Example", "example-host", []string{}, 1); err != nil {
		t.Fatal(err)
	}
	if bodies[2]["flow_execution_consent"] != "never" {
		t.Fatal("explicit consent omitted on resume")
	}
}

func TestRunnerSetupUnattendedExplicitConsent(t *testing.T) {
	testenv.SetTempHome(t)
	shortenRunnerOnlineWait(t)
	installs := stubRunnerInstall(t)
	var out bytes.Buffer
	tty := false
	request := runnerSetupRequest{
		Out: &out, In: strings.NewReader(""), TTY: &tty, Yes: true,
		Env:       runnerSetupEnv{OnDemand: true},
		Installed: func() bool { return false },
	}
	if err := performRunnerSetup(request); err == nil {
		t.Fatal("unattended setup accepted without consent")
	}
	if *installs != 0 {
		t.Fatal("installed before validating consent")
	}
	request.Consent = "never"
	if err := performRunnerSetup(request); err != nil {
		t.Fatal(err)
	}
	if *installs != 1 {
		t.Fatalf("installs = %d", *installs)
	}
	if strings.Contains(out.String(), "Install a Preloop runner on this machine?") {
		t.Fatal("--yes asked installation question")
	}
	got, _, err := config.RunnerFlowExecutionConsent()
	if err != nil || got != "never" {
		t.Fatalf("consent = %q, error %v", got, err)
	}
}

func TestRunnerEnableAppliesOnlyExplicitConsentBeforeInstall(t *testing.T) {
	cases := []struct {
		name     string
		existing bool
		choice   string
		refused  bool
	}{
		{name: "existing runner always", existing: true, choice: "always"},
		{name: "existing runner asks", existing: true, choice: "ask_me"},
		{name: "existing runner never", existing: true, choice: "never"},
		{name: "saved personal value preserves console", existing: true},
		{name: "new runner choice waits for registration", choice: "never"},
		{name: "server refusal prevents install", existing: true, choice: "always", refused: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			testenv.SetTempHome(t)
			oldToken, oldURL := FlagToken, FlagURL
			oldInstall, oldOverride := setupInstallRunner, runnerConsentOverride
			t.Cleanup(func() {
				FlagToken, FlagURL = oldToken, oldURL
				setupInstallRunner, runnerConsentOverride = oldInstall, oldOverride
			})
			if err := config.SetRunnerFlowExecutionConsent("always"); err != nil {
				t.Fatal(err)
			}
			if tc.existing {
				if err := writeRunnerState(&runnerState{ID: "runner-example", Token: "runner-token"}); err != nil {
					t.Fatal(err)
				}
			}
			patches, installed := 0, false
			serverConsent := "ask_me"
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				patches++
				if r.Method != http.MethodPatch || r.URL.Path != "/api/v1/runners/runner-example/consent" {
					t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
				}
				if r.Header.Get("Authorization") != "Bearer example-owner-token" {
					t.Error("consent update did not use the authenticated owner's credential")
				}
				var body map[string]string
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["flow_execution_consent"] != tc.choice {
					t.Errorf("consent = %q", body["flow_execution_consent"])
				}
				if tc.refused {
					http.Error(w, `{"detail":"Only the runner owner can change consent"}`, http.StatusForbidden)
					return
				}
				serverConsent = body["flow_execution_consent"]
				_ = json.NewEncoder(w).Encode(map[string]string{"id": "runner-example"})
			}))
			defer server.Close()
			FlagToken, FlagURL = "example-owner-token", server.URL
			setupInstallRunner = func(out io.Writer) error {
				installed = true
				if tc.existing && tc.choice != "" && serverConsent != tc.choice {
					t.Error("service installed before consent was applied")
				}
				return nil
			}
			command := &cobra.Command{}
			command.Flags().String("flow-execution-consent", tc.choice, "")
			err := runRunnerEnable(command, nil)
			if tc.refused {
				if err == nil || installed {
					t.Fatalf("refused update: err %v, installed %v", err, installed)
				}
			} else if err != nil || !installed {
				t.Fatalf("enable: err %v, installed %v", err, installed)
			}
			expectedPatches := 0
			if tc.existing && tc.choice != "" {
				expectedPatches = 1
			}
			if patches != expectedPatches {
				t.Fatalf("patches = %d, want %d", patches, expectedPatches)
			}
			if tc.choice == "" && serverConsent != "ask_me" {
				t.Fatal("saved personal consent overwrote console choice")
			}
		})
	}
}

func TestRunnerSetupAppliesChosenConsentBeforeReinstall(t *testing.T) {
	cases := []struct {
		name     string
		explicit string
		answer   string
		expected string
		refused  bool
	}{
		{name: "explicit unattended choice", explicit: "never", expected: "never"},
		{name: "interactive changed choice", answer: "1\n", expected: "ask_me"},
		{name: "interactive accepted default", answer: "\n", expected: "always"},
		{name: "refused update stops reinstall", explicit: "always", expected: "always", refused: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			testenv.SetTempHome(t)
			oldToken, oldURL := FlagToken, FlagURL
			oldInstall, oldStart := setupInstallRunner, setupStartRunner
			t.Cleanup(func() {
				FlagToken, FlagURL = oldToken, oldURL
				setupInstallRunner, setupStartRunner = oldInstall, oldStart
			})
			if err := config.SetRunnerFlowExecutionConsent("always"); err != nil {
				t.Fatal(err)
			}
			if err := writeRunnerState(&runnerState{ID: "runner-example", Token: "runner-token"}); err != nil {
				t.Fatal(err)
			}
			patches, installed := 0, false
			serverConsent := "ask_me"
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				patches++
				if r.Method != http.MethodPatch || r.URL.Path != "/api/v1/runners/runner-example/consent" {
					t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
				}
				var body map[string]string
				if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
					t.Error(err)
				}
				if body["flow_execution_consent"] != tc.expected {
					t.Errorf("consent = %q, want %q", body["flow_execution_consent"], tc.expected)
				}
				if tc.refused {
					http.Error(w, `{"detail":"Only the runner owner can change consent"}`, http.StatusForbidden)
					return
				}
				serverConsent = body["flow_execution_consent"]
				_ = json.NewEncoder(w).Encode(map[string]string{"id": "runner-example"})
			}))
			defer server.Close()
			FlagToken, FlagURL = "example-owner-token", server.URL
			setupInstallRunner = func(out io.Writer) error {
				installed = true
				if serverConsent != tc.expected || patches != 1 {
					t.Error("service installed before chosen consent was applied")
				}
				return nil
			}
			// Avoid launching a service or polling a live runner during this test.
			setupStartRunner = func() error { return fmt.Errorf("test service stays stopped") }
			tty := tc.explicit == ""
			var out bytes.Buffer
			err := installRunnerFromSetup(runnerSetupRequest{
				In: strings.NewReader(tc.answer), Out: &out, TTY: &tty, Consent: tc.explicit,
			})
			if tc.refused {
				if err == nil || installed {
					t.Fatalf("refused update: err %v, installed %v", err, installed)
				}
			} else if err != nil || !installed {
				t.Fatalf("setup: err %v, installed %v", err, installed)
			}
			if patches != 1 {
				t.Fatalf("patches = %d", patches)
			}
		})
	}
}
