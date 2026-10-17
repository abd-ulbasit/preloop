package cmd

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
)

// Exercise the API's collision response through sync and the config writer.
// The occupied alias belongs to a different upstream and cannot be reused.
func TestManagedGatewayAliasCollision(t *testing.T) {
	for _, tc := range []struct{ name, provider, model, file string }{
		{"Claude Code", "anthropic", "claude-sonnet-4-5", "settings.json"},
		{"Codex CLI", "openai", "gpt-4o-mini", "config.toml"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			t.Setenv("PRELOOP_DISABLE_TELEMETRY", "true")
			assigned := tc.model + "-2"
			models := []aiModelResponse{{ID: "occupied", ProviderName: tc.provider, ModelIdentifier: "another-model", MetaData: map[string]interface{}{"gateway": map[string]interface{}{"enabled": true, "model_alias": tc.model}}}}
			creates := 0
			allowed := ""
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				switch {
				case r.Method == http.MethodGet && r.URL.Path == "/api/v1/ai-models":
					_ = json.NewEncoder(w).Encode(models)
				case r.Method == http.MethodPost && r.URL.Path == "/api/v1/ai-models":
					creates++
					var req aiModelCreateRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
						w.WriteHeader(400)
						return
					}
					req.MetaData["gateway"].(map[string]interface{})["model_alias"] = assigned
					model := aiModelResponse{ID: "imported", Name: req.Name, ProviderName: req.ProviderName, ModelIdentifier: req.ModelIdentifier, APIEndpoint: req.APIEndpoint, HasAPIKey: true, MetaData: req.MetaData}
					models = append(models, model)
					_ = json.NewEncoder(w).Encode(model)
				case r.Method == http.MethodPut && r.URL.Path == "/api/v1/ai-models/imported":
					var req struct {
						MetaData map[string]interface{} `json:"meta_data"`
					}
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
						w.WriteHeader(400)
						return
					}
					if got := req.MetaData["gateway"].(map[string]interface{})["model_alias"]; got != assigned {
						t.Errorf("repeat onboarding tried to rename assigned alias: %v", got)
					}
					models[1].MetaData = req.MetaData
					_ = json.NewEncoder(w).Encode(models[1])
				case r.Method == http.MethodPut && r.URL.Path == "/api/v1/agents/synthetic-agent/model-bindings":
					var req managedAgentModelBindingSyncRequest
					if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
						t.Error(err)
						w.WriteHeader(400)
						return
					}
					if len(req.Bindings) != 1 || req.Bindings[0].AIModelID != models[1].ID {
						t.Errorf("unexpected bindings: %+v", req)
						w.WriteHeader(400)
						return
					}
					allowed = gatewayAliasForAIModel(models[1])
					if req.Bindings[0].GatewayAlias != allowed {
						t.Errorf("persisted binding alias %q differs from allowed alias %q", req.Bindings[0].GatewayAlias, allowed)
					}
					_ = json.NewEncoder(w).Encode([]managedAgentModelBindingSummary{})
				case r.Method == http.MethodPost && r.URL.Path == "/inference":
					var req struct {
						Model string `json:"model"`
					}
					_ = json.NewDecoder(r.Body).Decode(&req)
					if req.Model != allowed {
						http.Error(w, "model_not_authorized", 400)
						return
					}
					_ = json.NewEncoder(w).Encode(map[string]bool{"ok": true})
				default:
					t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
					w.WriteHeader(404)
				}
			}))
			defer server.Close()
			client := api.NewClientWithToken(server.URL, "synthetic-token")
			agent := AgentConfig{Name: tc.name, ConfigPath: filepath.Join(t.TempDir(), tc.file)}
			for attempt := 0; attempt < 2; attempt++ {
				// Explicit selection on each run still names the original selector.
				upstream := &managedGatewayUpstream{SourceAgent: tc.name, ProviderName: tc.provider, ModelIdentifier: tc.model, ManagedModelAlias: tc.model, APIEndpoint: "https://example.com/v1", APIKey: "synthetic-key"}
				model, _, err := syncManagedGatewayAIModel(client, &managedAgentSummary{ID: "synthetic-agent"}, agent, upstream, server.URL)
				if err != nil {
					t.Fatal(err)
				}
				if _, err := syncManagedAgentModelBindings(client, "synthetic-agent", []managedAgentModelBindingSyncItem{{AIModelID: model.ID, BindingType: "configured", ConfigKey: managedGatewayBindingConfigKey(agent), GatewayAlias: upstream.ManagedModelAlias, IsPrimary: true, Status: "gateway_ready"}}); err != nil {
					t.Fatal(err)
				}
				plan, err := applyManagedGatewayForAgent(managedMCPEnrollmentPlan{Agent: agent, ManagedDocument: map[string]interface{}{}}, agent, server.URL, "synthetic-token", upstream.ManagedModelAlias, nil, true)
				if err != nil {
					t.Fatal(err)
				}
				if err := writeAgentConfigDocument(agent, plan.ManagedDocument); err != nil {
					t.Fatal(err)
				}
				doc, err := loadAgentConfigDocument(agent)
				if err != nil {
					t.Fatal(err)
				}
				selector, _ := doc["model"].(string)
				if tc.name == "Claude Code" {
					selector, _ = doc["env"].(map[string]interface{})["ANTHROPIC_DEFAULT_SONNET_MODEL"].(string)
				}
				if selector != assigned || plan.ManagedModelAlias != assigned || upstream.ManagedModelAlias != gatewayAliasForAIModel(*model) {
					t.Errorf("attempt %d: written=%q plan=%q binding=%q allowed=%q", attempt, selector, plan.ManagedModelAlias, upstream.ManagedModelAlias, gatewayAliasForAIModel(*model))
				}
				if err := client.Post("/inference", map[string]string{"model": selector}, nil); err != nil {
					t.Errorf("written selector rejected: %v", err)
				}
			}
			if creates != 1 {
				t.Errorf("created %d models on repeat onboarding", creates)
			}
		})
	}
}

func TestClaudeSiblingAliasCollision(t *testing.T) {
	t.Setenv("PRELOOP_DISABLE_TELEMETRY", "true")
	originalToken := claudeLiveAccessToken
	claudeLiveAccessToken = func() string { return "" }
	t.Cleanup(func() { claudeLiveAccessToken = originalToken })
	models := []aiModelResponse{}
	creates := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			_ = json.NewEncoder(w).Encode(models)
		case http.MethodPost:
			creates++
			var req aiModelCreateRequest
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				t.Error(err)
				w.WriteHeader(400)
				return
			}
			gateway := req.MetaData["gateway"].(map[string]interface{})
			gateway["model_alias"] = gateway["model_alias"].(string) + "-2"
			model := aiModelResponse{ID: req.ModelIdentifier, Name: req.Name, ProviderName: req.ProviderName, ModelIdentifier: req.ModelIdentifier, CredentialsSecretID: req.CredentialsSecretID, HasAPIKey: true, MetaData: req.MetaData}
			models = append(models, model)
			_ = json.NewEncoder(w).Encode(model)
		default:
			t.Errorf("unexpected request %s %s", r.Method, r.URL.Path)
			w.WriteHeader(400)
		}
	}))
	defer server.Close()
	primary := claudeFamilyPrimaryForTest("synthetic-secret-reference")
	upstream := &managedGatewayUpstream{ProviderName: "anthropic", ManagedModelAlias: gatewayAliasForAIModel(*primary)}
	for attempt := 0; attempt < 2; attempt++ {
		bindings, aliases, _, err := syncClaudeSiblingFamilyAIModels(api.NewClientWithToken(server.URL, "synthetic-token"), &managedAgentSummary{ID: "synthetic-agent"}, AgentConfig{Name: "Claude Code"}, upstream, primary, server.URL)
		if err != nil {
			t.Fatal(err)
		}
		if len(bindings) == 0 || len(bindings) != len(models) || len(aliases) != len(models) {
			t.Fatalf("missing siblings: %v %v", bindings, aliases)
		}
		for i, model := range models {
			assigned := gatewayAliasForAIModel(model)
			if bindings[i].GatewayAlias != assigned || aliases[i] != assigned {
				t.Errorf("sibling alias mismatch: binding=%q config=%q allowed=%q", bindings[i].GatewayAlias, aliases[i], assigned)
			}
		}
	}
	if creates != len(models) {
		t.Errorf("repeat onboarding created another sibling")
	}
}
