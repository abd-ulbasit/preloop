package cmd

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

func TestInventoryRouteClassification(t *testing.T) {
	for _, tc := range []struct {
		name, doc                       string
		managed, remote, stdio, unknown int
	}{
		{"mixed", `{"mcpServers":{"preloop":{"url":"https://example.com/mcp/v1"},"synthetic-remote":{"url":"https://user:synthetic-secret@example.com/private?token=synthetic-secret","headers":{"X-Token":"synthetic-secret"}},"synthetic-local":{"command":"/private/synthetic-secret","args":["synthetic-secret"],"env":{"TOKEN":"synthetic-secret"}},"odd":{}}}`, 1, 1, 1, 1},
		{"empty", `{}`, 0, 0, 0, 0},
		{"invalid-value", `{"mcpServers":{"odd":false}}`, 0, 0, 0, 1},
		{"stale", `{"mcpServers":{"preloop":{"url":"https://example.com/stale"}}}`, 0, 0, 0, 1},
		{"disabled", `{"mcpServers":{"preloop":{"url":"https://example.com/mcp/v1","enabled":false}}}`, 0, 0, 0, 1},
		{"name-only", `{"mcpServers":{"preloop":{}}}`, 0, 0, 0, 1},
		{"bridge", `{"mcpServers":{"preloop":{"command":"npx","args":["-y","mcp-remote","https://example.com/mcp/v1","--header","Authorization: synthetic-secret"]}}}`, 1, 0, 0, 0},
		{"local-bridge", `{"mcpServers":{"third-party":{"command":"npx","args":["mcp-remote","https://example.com/mcp/v1"]}}}`, 0, 0, 1, 0},
		{"opencode-local", `{"mcpServers":{"local":{"type":"local","command":["node","synthetic-secret"]}}}`, 0, 0, 1, 0},
		{"contradictory", `{"mcpServers":{"a":{"command":"node","url":"https://example.com"},"b":{"url":"file:///tmp/example"},"c":{"url":"https://example.com","transport":"stdio"},"d":{"url":"https://example.com","type":"sse","transport":"stdio"}}}`, 0, 0, 0, 4},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got, err := inventoryConfigRoutes("Cursor", "mcp.json", []byte(tc.doc))
			if err != nil {
				t.Fatal(err)
			}
			want := inventoryMCPRoutes{Managed: tc.managed, DirectRemote: tc.remote, DirectStdio: tc.stdio, Unknown: tc.unknown, Completeness: "complete"}
			if got != want {
				t.Fatalf("got %+v want %+v", got, want)
			}
			wire, _ := json.Marshal(got)
			for _, secret := range []string{"synthetic-secret", "synthetic-local", "synthetic-remote", "example.com", "Authorization"} {
				if strings.Contains(string(wire), secret) {
					t.Fatalf("leaked: %s", wire)
				}
			}
		})
	}
}

func TestInventoryRoutesAcrossConfigFormats(t *testing.T) {
	want := inventoryMCPRoutes{Managed: 1, DirectRemote: 1, DirectStdio: 1, Unknown: 1, Completeness: "complete"}
	for _, tc := range []struct{ app, path, doc string }{
		{"Codex CLI", "config.toml", "[mcp_servers.preloop]\nurl='https://example.com/mcp/v1'\n[mcp_servers.remote]\nurl='https://example.com/private'\n[mcp_servers.local]\ncommand='node'\nargs=['synthetic-secret']\n[mcp_servers.odd]\n"},
		{"Hermes", "config.yaml", "mcp_servers:\n  preloop:\n    url: https://example.com/mcp/v1\n  remote:\n    url: https://example.com/private\n  local:\n    command: node\n    args: [synthetic-secret]\n  odd: {}\n"},
		{"OpenClaw", "config.json5", `{mcp:{servers:{preloop:{url:"https://example.com/mcp/v1"},remote:{url:"https://example.com/private"},local:{command:"node"},odd:{}}}}`},
	} {
		t.Run(tc.path, func(t *testing.T) {
			got, err := inventoryConfigRoutes(tc.app, tc.path, []byte(tc.doc))
			if err != nil || got != want {
				t.Fatalf("got %+v, %v", got, err)
			}
		})
	}
}

func TestInventoryRoutePlanPreservesThirdParty(t *testing.T) {
	home := t.TempDir()
	path := writeInventoryFixture(t, home, ".cursor/mcp.json", `{"mcpServers":{"remote":{"url":"https://example.com/private","headers":{"X-Token":"synthetic-secret"}},"local":{"command":"node","args":["synthetic-secret"]},"preloop":{"url":"https://example.com/stale"}}}`)
	inventoryTestDependencies(t, home)
	agent := AgentConfig{Name: "Cursor", ConfigPath: path}
	plan, err := buildManagedMCPEnrollmentPlan(agent, "https://example.com", "synthetic-token")
	if err != nil {
		t.Fatal(err)
	}
	before := lookupMCPServerContainer(plan.DiscoveredDocument)
	after := lookupMCPServerContainer(plan.ManagedDocument)
	for _, name := range []string{"remote", "local"} {
		if !reflect.DeepEqual(before[name], after[name]) {
			t.Fatalf("changed third party %s", name)
		}
	}
	if plan.MCPRoutes != (inventoryMCPRoutes{Managed: 1, DirectRemote: 1, DirectStdio: 1, Completeness: "complete"}) {
		t.Fatalf("plan: %+v", plan.MCPRoutes)
	}
	summary := inventoryRouteSummary(plan.MCPRoutes)
	for _, text := range []string{"bypass Preloop MCP policy", "Only calls through the managed entry", "do not establish observed usage"} {
		if !strings.Contains(summary, text) {
			t.Fatal(summary)
		}
	}
	if strings.Contains(summary, "synthetic") || strings.Contains(summary, "example.com") {
		t.Fatal(summary)
	}
}

func TestInventoryDiscoveryRetainsUnknownRawEntries(t *testing.T) {
	home := t.TempDir()
	path := writeInventoryFixture(t, home, ".cursor/mcp.json", `{"mcpServers":{"preloop":{"url":"https://example.com/mcp/v1"},"remote":{"url":"https://example.com/private"},"malformed":false}}`)
	deps := inventoryTestDependencies(t, home)
	// The legacy parser omits the malformed entry. The safe route facts must not.
	parsed, err := parseGenericMCP(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(parsed) != 2 {
		t.Fatalf("fixture: %+v", parsed)
	}
	agent := AgentConfig{Name: "Cursor", MCPServers: parsed, inventoryRoutes: inventoryReadConfigRoutes("Cursor", path)}
	row := safeDiscoveryJSON([]AgentConfig{agent})[0]
	if row.MCPRoutes.Managed != 1 || row.MCPRoutes.DirectRemote != 1 || row.MCPRoutes.Unknown != 1 || row.MCPRoutes.Completeness != "complete" {
		t.Fatalf("lost raw declaration: %+v", row)
	}
	scan := collectAgentInventory(deps)
	for _, app := range scan.Apps {
		if app.AppID == "cursor" {
			if app.MCPRoutes != row.MCPRoutes {
				t.Fatalf("classifier drift: %+v %+v", app.MCPRoutes, row.MCPRoutes)
			}
			return
		}
	}
	t.Fatal("cursor missing")
}

func TestInventoryRoutesIncompleteContainer(t *testing.T) {
	for _, doc := range []string{`{"mcpServers":false}`, `{"mcp":null}`, `{"mcp":{"servers":"synthetic-secret"}}`} {
		got, err := inventoryConfigRoutes("Cursor", "mcp.json", []byte(doc))
		if err != nil || got.Completeness != "partial" {
			t.Fatalf("malformed container: %+v %v", got, err)
		}
	}
}

func TestInventoryRoutesMatchAdapterDeclarations(t *testing.T) {
	for _, spec := range agentSpecs {
		t.Run(spec.Name, func(t *testing.T) {
			adapter := managedMCPAdapterForAgent(AgentConfig{Name: spec.Name})
			entry := adapter.BuildManagedServer("https://example.com", "synthetic-token")
			got := inventoryRoutesForDocument(map[string]interface{}{"mcpServers": map[string]interface{}{"preloop": entry}})
			if got.Managed != 1 || got.Unknown != 0 {
				t.Fatalf("adapter entry not recognized: %+v", got)
			}
		})
	}
}
