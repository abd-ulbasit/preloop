package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
)

func TestEnsureDiscoveredRemoteServersWarnsOnUnhealthyImports(t *testing.T) {
	for _, reuse := range []bool{false, true} {
		name := "new"
		if reuse {
			name = "reused"
		}
		t.Run(name, func(t *testing.T) {
			t.Setenv("PRELOOP_DISABLE_TELEMETRY", "true")
			response := mcpServerResponse{ID: "synthetic-server", Name: "example", Status: "error", LastError: "URL contains token=private"}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", "application/json")
				if r.Method == http.MethodGet {
					servers := []mcpServerResponse{}
					if reuse {
						servers = append(servers, response)
					}
					if err := json.NewEncoder(w).Encode(servers); err != nil {
						t.Error(err)
					}
					return
				}
				if err := json.NewEncoder(w).Encode(response); err != nil {
					t.Error(err)
				}
			}))
			defer server.Close()
			client, err := api.NewClient("synthetic-token", server.URL)
			if err != nil {
				t.Fatal(err)
			}
			result, err := ensureDiscoveredRemoteServers(client, AgentConfig{MCPServers: map[string]MCPDef{"example": {URL: "https://example.com/mcp"}}}, server.URL)
			if err != nil {
				t.Fatal(err)
			}
			if len(result.Warnings) != 1 || !strings.Contains(result.Warnings[0], "could not connect") {
				t.Fatalf("unhealthy registration needs an actionable warning: %+v", result)
			}
			if strings.Contains(result.Warnings[0], "private") {
				t.Fatal("warning exposed stored credentials")
			}
			if reuse && len(result.Reused) != 1 || !reuse && len(result.Added) != 1 {
				t.Fatalf("keep registration for retry: %+v", result)
			}
		})
	}
}

func TestImportedMCPServerWarningDoesNotWarnForHealthyServer(t *testing.T) {
	for _, lastError := range []string{"", "historical tool transport failure"} {
		if warning := importedMCPServerWarning("example", mcpServerResponse{Status: "active", LastError: lastError}); warning != "" {
			t.Fatalf("healthy server with historical error %q warned: %q", lastError, warning)
		}
	}
}
