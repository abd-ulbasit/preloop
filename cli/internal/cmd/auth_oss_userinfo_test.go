package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
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
	t.Setenv("PRELOOP_NO_RUNNER", "")
	stubRunnerInstall(t)
	oldTerm := loginTerminal
	loginTerminal = func() bool { return true }
	t.Cleanup(func() { loginTerminal = oldTerm })

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
