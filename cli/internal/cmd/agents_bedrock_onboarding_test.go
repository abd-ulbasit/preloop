package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
	"github.com/preloop/preloop/cli/internal/testenv"
)

func isolateBedrockOnboarding(t *testing.T) string {
	t.Helper()
	home := t.TempDir()
	testenv.SetHome(t, home)
	for _, key := range []string{"AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "CLAUDE_CODE_USE_BEDROCK", "ANTHROPIC_MODEL"} {
		t.Setenv(key, "")
	}
	t.Setenv("PRELOOP_DISABLE_TELEMETRY", "true")
	return home
}

func assertBedrockOnboardingSecret(t *testing.T, secret, token, region string) {
	t.Helper()
	var payload map[string]string
	if err := json.Unmarshal([]byte(secret), &payload); err != nil {
		t.Fatalf("region missing from imported credentials: %v", err)
	}
	if payload["aws_bearer_token_bedrock"] != token || payload["aws_region_name"] != region {
		t.Fatalf("unexpected credential payload fields: %v", payload)
	}
}

func TestClaudeBedrockOnboardingPreservesProfileAndRegion(t *testing.T) {
	for _, model := range []string{"us.anthropic.claude-sonnet-4-5-20250929-v1:0", "arn:aws:bedrock:eu-west-1:123456789012:application-inference-profile/example"} {
		t.Run(model, func(t *testing.T) {
			home := isolateBedrockOnboarding(t)
			document := map[string]interface{}{"model": model, "env": map[string]interface{}{"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": "eu-west-1", "AWS_BEARER_TOKEN_BEDROCK": "example-token"}}
			raw, _ := json.Marshal(document)
			path := filepath.Join(home, "claude.json")
			if err := os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			upstream, err := parseClaudeManagedGatewayUpstream(AgentConfig{Name: "Claude Code", ConfigPath: path})
			if err != nil || upstream == nil {
				t.Fatalf("missing upstream: %v", err)
			}
			if upstream.ModelIdentifier != model {
				t.Fatalf("profile changed: %q", upstream.ModelIdentifier)
			}
			assertBedrockOnboardingSecret(t, upstream.APIKey, "example-token", "eu-west-1")
		})
	}
}

func TestOpenClawBedrockOnboardingPreservesProfileAndAWSConfigRegion(t *testing.T) {
	home := isolateBedrockOnboarding(t)
	t.Setenv("AWS_BEARER_TOKEN_BEDROCK", "example-token")
	if err := os.MkdirAll(filepath.Join(home, ".aws"), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(home, ".aws", "config"), []byte("[default]\nregion = eu-west-1\n"), 0600); err != nil {
		t.Fatal(err)
	}
	model := "arn:aws:bedrock:eu-west-1:123456789012:application-inference-profile/example"
	document := map[string]interface{}{"models": map[string]interface{}{"providers": map[string]interface{}{"bedrock": map[string]interface{}{"api": "bedrock-converse-stream"}}}}
	parsed := resolveOpenClawConfiguredModel(document, "primary", "bedrock/"+model, true)
	if parsed.ModelID != model {
		t.Fatalf("profile changed: %q", parsed.ModelID)
	}
	assertBedrockOnboardingSecret(t, parsed.ProviderAPIKey, "example-token", "eu-west-1")
}

func TestOpenCodeBedrockOnboardingImportsProviderCredentials(t *testing.T) {
	home := isolateBedrockOnboarding(t)
	model := "arn:aws:bedrock:eu-west-1:123456789012:application-inference-profile/example"
	document := map[string]interface{}{"model": "amazon-bedrock/" + model, "provider": map[string]interface{}{"amazon-bedrock": map[string]interface{}{"options": map[string]interface{}{"region": "eu-west-1", "apiKey": "example-token"}}}}
	raw, _ := json.Marshal(document)
	path := filepath.Join(home, "opencode.json")
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	upstream, err := parseOpenCodeManagedGatewayUpstream(AgentConfig{Name: "OpenCode", ConfigPath: path})
	if err != nil || upstream == nil {
		t.Fatalf("missing upstream: %v", err)
	}
	if upstream.ModelIdentifier != model {
		t.Fatalf("profile changed: %q", upstream.ModelIdentifier)
	}
	assertBedrockOnboardingSecret(t, upstream.APIKey, "example-token", "eu-west-1")
}

func TestOpenCodeBedrockOnboardingPrefersExplicitIAMOptions(t *testing.T) {
	home := isolateBedrockOnboarding(t)
	t.Setenv("AWS_ACCESS_KEY_ID", "ambient-key")
	t.Setenv("AWS_SECRET_ACCESS_KEY", "ambient-secret")
	document := map[string]interface{}{"model": "amazon-bedrock/us.amazon.nova-micro-v1:0", "provider": map[string]interface{}{"amazon-bedrock": map[string]interface{}{"options": map[string]interface{}{"region": "eu-west-1", "accessKeyId": "explicit-key", "secretAccessKey": "explicit-secret", "sessionToken": "example-session"}}}}
	raw, _ := json.Marshal(document)
	path := filepath.Join(home, "opencode.json")
	if err := os.WriteFile(path, raw, 0600); err != nil {
		t.Fatal(err)
	}
	upstream, err := parseOpenCodeManagedGatewayUpstream(AgentConfig{Name: "OpenCode", ConfigPath: path})
	if err != nil || upstream == nil {
		t.Fatalf("missing upstream: %v", err)
	}
	var payload bedrockCredentialPayload
	if err := json.Unmarshal([]byte(upstream.APIKey), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.AWSAccessKeyID != "explicit-key" || payload.AWSSecretAccessKey != "explicit-secret" || payload.AWSSessionToken != "example-session" || payload.AWSRegionName != "eu-west-1" {
		t.Fatal("explicit IAM options were not preserved")
	}
}

func TestOpenCodeBedrockOnboardingImportsEnvironmentBearer(t *testing.T) {
	home := isolateBedrockOnboarding(t)
	t.Setenv("AWS_REGION", "eu-west-1")
	t.Setenv("AWS_BEARER_TOKEN_BEDROCK", "example-token")
	path := filepath.Join(home, "opencode.json")
	if err := os.WriteFile(path, []byte(`{"model":"amazon-bedrock/us.amazon.nova-micro-v1:0"}`), 0600); err != nil {
		t.Fatal(err)
	}
	upstream, err := parseOpenCodeManagedGatewayUpstream(AgentConfig{Name: "OpenCode", ConfigPath: path})
	if err != nil || upstream == nil {
		t.Fatalf("missing upstream: %v", err)
	}
	assertBedrockOnboardingSecret(t, upstream.APIKey, "example-token", "eu-west-1")
}

func TestBedrockOnboardingRegistrationPreservesProfileAndRegion(t *testing.T) {
	isolateBedrockOnboarding(t)
	writes := []recordedAIModelWrite{}
	server := newCodexFamilyLineageServer(t, nil, &writes)
	defer server.Close()
	model := "arn:aws:bedrock:eu-west-1:123456789012:application-inference-profile/example"
	upstream := &managedGatewayUpstream{ProviderName: "amazon-bedrock", ModelIdentifier: model, ManagedModelAlias: "amazon-bedrock/" + model, APIKey: bedrockOnboardingBearerSecret("example-token", "eu-west-1")}
	_, _, err := syncManagedGatewayAIModel(api.NewClientWithToken(server.URL, "example-preloop-token"), &managedAgentSummary{ID: "example-agent"}, AgentConfig{Name: "Claude Code"}, upstream, server.URL+"/api/v1/gateway")
	if err != nil {
		t.Fatal(err)
	}
	if len(writes) != 1 {
		t.Fatalf("expected one registration, got %d", len(writes))
	}
	body := writes[0].Body
	if body["model_identifier"] != model {
		t.Fatal("profile lost during registration")
	}
	secret, _ := body["api_key"].(string)
	assertBedrockOnboardingSecret(t, secret, "example-token", "eu-west-1")
	meta, _ := body["meta_data"].(map[string]interface{})
	runtime, _ := meta["provider_runtime"].(map[string]interface{})
	if runtime["region"] != "eu-west-1" || runtime["auth_method"] != "api_key" {
		t.Fatalf("console routing settings missing: %v", runtime)
	}
	if _, ok := runtime["aws_bearer_token_bedrock"]; ok {
		t.Fatal("secret leaked into metadata")
	}

}
