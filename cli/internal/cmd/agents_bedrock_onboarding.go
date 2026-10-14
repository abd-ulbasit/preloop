package cmd

import (
	"encoding/json"
	"net/url"
	"strings"

	"gopkg.in/ini.v1"
)

// resolveBedrockOnboardingRegion preserves the local AWS region even when
// bearer-token authentication bypasses IAM credential discovery.
func resolveBedrockOnboardingRegion(document map[string]interface{}, providerID, region string) string {
	if value := strings.TrimSpace(region); value != "" {
		return value
	}
	for _, key := range []string{"AWS_REGION", "AWS_DEFAULT_REGION"} {
		if value := resolveOpenClawEnvVar(document, key); value != "" {
			return value
		}
	}
	for _, key := range []string{"baseUrl", "baseURL"} {
		endpoint := lookupString(document, "models", "providers", providerID, key)
		if parsed, err := url.Parse(endpoint); err == nil {
			host := strings.TrimPrefix(parsed.Hostname(), "bedrock-runtime.")
			if host != parsed.Hostname() && strings.HasSuffix(host, ".amazonaws.com") {
				return strings.TrimSuffix(host, ".amazonaws.com")
			}
		}
	}
	_, configPath := resolveOpenClawAWSConfigPaths(document)
	if configFile, err := ini.Load(configPath); err == nil {
		profile := resolveOpenClawAWSProfile(document, providerID)
		if profile != "default" {
			profile = "profile " + profile
		}
		return strings.TrimSpace(configFile.Section(profile).Key("region").String())
	}
	return ""
}

// bedrockOnboardingBearerSecret carries region alongside a token in the
// encrypted credential format already understood by the gateway.
func bedrockOnboardingBearerSecret(secret, region string) string {
	if strings.TrimSpace(region) == "" {
		return secret
	}
	var existing map[string]interface{}
	if json.Unmarshal([]byte(secret), &existing) == nil && existing != nil {
		if existing["aws_region_name"] == nil {
			existing["aws_region_name"] = region
		}
		encoded, _ := json.Marshal(existing)
		return string(encoded)
	}
	encoded, _ := json.Marshal(map[string]string{"aws_bearer_token_bedrock": secret, "aws_region_name": region})
	return string(encoded)
}

// mergeBedrockOnboardingRuntime keeps console-editable AWS routing settings
// alongside the encrypted credential, without putting secrets in metadata.
func mergeBedrockOnboardingRuntime(meta map[string]interface{}, provider, secret string) map[string]interface{} {
	if !openClawProviderUsesAmbientCredentials(provider, provider) {
		return meta
	}
	var payload map[string]interface{}
	if json.Unmarshal([]byte(secret), &payload) != nil {
		return meta
	}
	runtime, _ := asObjectMap(meta["provider_runtime"])
	cloned, _ := deepCopyMap(runtime)
	if cloned == nil {
		cloned = map[string]interface{}{}
	}
	if region, ok := payload["aws_region_name"].(string); ok && strings.TrimSpace(region) != "" {
		cloned["region"] = strings.TrimSpace(region)
	}
	if token, ok := payload["aws_bearer_token_bedrock"].(string); ok && token != "" {
		cloned["auth_method"] = "api_key"
	} else if key, ok := payload["aws_access_key_id"].(string); ok && key != "" {
		cloned["auth_method"] = "iam"
	}
	meta["provider_runtime"] = cloned
	return meta
}
