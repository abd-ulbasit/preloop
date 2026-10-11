package cmd

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/testenv"
)

// fakeOSSServer serves only the OSS auth user route (#1529).
func fakeOSSServer(t *testing.T, policy map[string]any) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/oauth/token":
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"access_token":"oss-access","refresh_token":"oss-refresh"}`))
		case authUserInfoPath:
			body := map[string]any{
				"id": "user-1", "account_id": "acct-1", "username": "ada",
				"email": "ada@example.com", "full_name": "Ada Lovelace",
				"email_verified": true,
			}
			if policy != nil {
				body["runner_policy"] = policy
			}
			_ = json.NewEncoder(w).Encode(body)
		default:
			http.NotFound(w, r)
		}
	}))
}

func TestLoginOnOSSOffersRunner(t *testing.T) {
	testenv.SetTempHome(t)
	restore := snapshotLoginFlags()
	defer restore()
	stubInteractiveLogin(t)

	server := fakeOSSServer(t, map[string]any{"requirement": "optional", "can_decide": true})
	defer server.Close()

	out := captureStdout(t, func() error {
		return finishOAuthLogin(server.URL, "code", "http://127.0.0.1/cb", false)
	})
	if !strings.Contains(out, "Logged in as: Ada Lovelace") {
		t.Fatalf("missing user line: %q", out)
	}
	if !strings.Contains(out, "Install a Preloop runner on this machine?") {
		t.Fatalf("runner prompt not shown on OSS: %q", out)
	}
}

func stubInteractiveLogin(t *testing.T) {
	t.Helper()
	t.Setenv("PRELOOP_NO_RUNNER", "")
	stubRunnerInstall(t)
	oldTerm := loginTerminal
	loginTerminal = func() bool { return true }
	t.Cleanup(func() { loginTerminal = oldTerm })
}

func captureStderr(t *testing.T, fn func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	old := os.Stderr
	os.Stderr = w
	fn()
	os.Stderr = old
	_ = w.Close()
	data, _ := io.ReadAll(r)
	return string(data)
}

func TestLoginOnOSSForbiddenPolicyHidesPrompt(t *testing.T) {
	testenv.SetTempHome(t)
	restore := snapshotLoginFlags()
	defer restore()
	stubInteractiveLogin(t)
	server := fakeOSSServer(t, map[string]any{"requirement": "forbidden", "can_decide": false})
	defer server.Close()

	out := captureStdout(t, func() error {
		return finishOAuthLogin(server.URL, "code", "http://127.0.0.1/cb", false)
	})
	if strings.Contains(out, "Install a Preloop runner on this machine?") {
		t.Fatalf("forbidden policy still prompted: %q", out)
	}
}

func TestLoginWarnsWhenProfileUnreadable(t *testing.T) {
	testenv.SetTempHome(t)
	restore := snapshotLoginFlags()
	defer restore()
	installs := stubRunnerInstall(t)
	oldTerm := loginTerminal
	loginTerminal = func() bool { return true }
	t.Cleanup(func() { loginTerminal = oldTerm })
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/oauth/token" {
			_, _ = w.Write([]byte(`{"access_token":"a","refresh_token":"r"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	var out string
	errOut := captureStderr(t, func() {
		out = captureStdout(t, func() error {
			return finishOAuthLogin(server.URL, "code", "http://127.0.0.1/cb", false)
		})
	})
	if !strings.Contains(errOut, "could not read account info") || !strings.Contains(errOut, "preloop runner setup") {
		t.Fatalf("missing warning, stderr %q", errOut)
	}
	if strings.Contains(out, "Install a Preloop runner on this machine?") || *installs != 0 {
		t.Fatalf("offered runner without a readable policy: %q", out)
	}
}

func TestFetchRunnerPolicyOnOSS(t *testing.T) {
	testenv.SetTempHome(t)
	server := fakeOSSServer(t, map[string]any{"requirement": "forbidden"})
	defer server.Close()
	saveRunnerSetupClient(t, server.URL)

	got := fetchRunnerPolicy()
	if got == nil || got.Requirement != "forbidden" {
		t.Fatalf("policy = %+v", got)
	}
}
