package cmd

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"sync"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
	"github.com/preloop/preloop/cli/internal/config"
	"github.com/preloop/preloop/cli/internal/testenv"
)

func TestRotateRunnerTokenKeepsDirectoryAddedDuringTheRequest(t *testing.T) {
	testenv.SetTempHome(t)
	seedRunnerState(t)
	stubRunnerService(t, false)
	added := authorizedDirectory{ID: "dir_mid", Path: t.TempDir(), Mode: authorizedDirModeWrite}
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// A `preloop runner dirs add` lands while the rotation is in flight.
		if err := writeAuthorizedDirectories([]authorizedDirectory{added}); err != nil {
			t.Error(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"id": revokeRunnerID, "token": "new-token"})
	}))
	defer server.Close()
	if err := rotateRunnerToken(api.NewClientWithToken(server.URL, "tok"), &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	state, err := readRunnerState()
	if err != nil {
		t.Fatal(err)
	}
	if state.Token != "new-token" || len(state.AuthorizedDirectories) != 1 || state.AuthorizedDirectories[0].ID != "dir_mid" {
		t.Fatalf("rotation must keep the concurrent change: %#v", state)
	}
}

func TestConcurrentAuthorizedDirectoryUpdatesAreNotLost(t *testing.T) {
	testenv.SetTempHome(t)
	seedRunnerState(t)
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			err := updateAuthorizedDirectories(func(existing []authorizedDirectory) ([]authorizedDirectory, error) {
				return append(existing, authorizedDirectory{ID: "dir_" + string(rune('a'+i)), Path: "/p", Mode: authorizedDirModeWrite}), nil
			})
			if err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	state, err := readRunnerState()
	if err != nil {
		t.Fatal(err)
	}
	if len(state.AuthorizedDirectories) != 8 || state.Token != "old-token" {
		t.Fatalf("lost update: %d entries, %#v", len(state.AuthorizedDirectories), state)
	}
}

func TestCredentialDirectoriesCannotBeAuthorized(t *testing.T) {
	home := canonicalTempHome(t)
	configDir, err := config.GetConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(configDir, "host-workspaces"), 0o700); err != nil {
		t.Fatal(err)
	}
	canonicalConfig, _ := filepath.EvalSymlinks(configDir)
	for _, dir := range []string{
		canonicalConfig,
		filepath.Join(canonicalConfig, "host-workspaces"),
		filepath.Join(home, ".ssh"),
		filepath.Join(home, ".ssh", "keys"),
		filepath.Join(home, ".config", "gh"),
		filepath.Join(home, ".copilot"),
	} {
		if err := refuseHomeDirectory(dir); err == nil {
			t.Fatalf("%s must be refused", dir)
		}
	}
	if err := refuseHomeDirectory(filepath.Join(home, "src", "ims")); err != nil {
		t.Fatalf("a project directory must be allowed: %v", err)
	}
}
