package cmd

import (
	"bytes"
	"encoding/json"
	"errors"

	"gopkg.in/yaml.v3"
)

var errInventoryConfig = errors.New("config_malformed")

// Count declarations using the shared validated route parser. Raw config,
// names and routing fields never escape this count-only compatibility helper.
func inventoryMCPServerCount(appName, path string, data []byte) (int, error) {
	_, count, err := inventoryConfigSummary(appName, path, data)
	return count, err
}

// inventoryLookupMCPContainer mirrors lookupMCPServerContainer: prefer a
// container that already has a "preloop" entry, otherwise the first recognised
// container. mcp.servers wins over mcp_servers. Bare top-level maps count for
// every agent when every entry matches looksLikeMCPServerEntry.
func inventoryLookupMCPContainer(
	doc map[string]json.RawMessage,
	decode func([]byte, any) error,
) map[string]json.RawMessage {
	var fallback map[string]json.RawMessage
	take := func(servers map[string]json.RawMessage) bool {
		if _, hasPreloop := servers["preloop"]; hasPreloop {
			fallback = servers
			return true
		}
		if fallback == nil {
			fallback = servers
		}
		return false
	}
	consider := func(raw json.RawMessage) bool {
		servers, ok := inventoryRawObject(decode, raw)
		if !ok {
			return false
		}
		return take(servers)
	}
	if raw, exists := doc["mcpServers"]; exists && consider(raw) {
		return fallback
	}
	if raw, exists := doc["servers"]; exists && consider(raw) {
		return fallback
	}
	if raw, exists := doc["mcp"]; exists {
		if mcp, ok := inventoryRawObject(decode, raw); ok {
			if nested, nestedExists := mcp["servers"]; nestedExists && consider(nested) {
				return fallback
			}
			if inventoryLooksLikeMCPServerContainer(mcp, decode) && take(mcp) {
				return fallback
			}
		}
	}
	if raw, exists := doc["mcp_servers"]; exists && consider(raw) {
		return fallback
	}
	if inventoryLooksLikeMCPServerContainer(doc, decode) {
		take(doc)
	}
	if fallback != nil {
		return fallback
	}
	return map[string]json.RawMessage{}
}

func inventoryRawObject(
	decode func([]byte, any) error,
	raw []byte,
) (map[string]json.RawMessage, bool) {
	var result map[string]json.RawMessage
	if len(bytes.TrimSpace(raw)) == 0 || decode(raw, &result) != nil || result == nil {
		return nil, false
	}
	return result, true
}

func inventoryLooksLikeMCPServerContainer(
	value map[string]json.RawMessage,
	decode func([]byte, any) error,
) bool {
	if len(value) == 0 {
		return false
	}
	for _, raw := range value {
		entry, ok := inventoryRawObject(decode, raw)
		if !ok || !inventoryLooksLikeMCPServerEntry(entry) {
			return false
		}
	}
	return true
}

// inventoryLooksLikeMCPServerEntry matches looksLikeMCPServerEntry: key
// presence only, so secret values are never decoded.
func inventoryLooksLikeMCPServerEntry(value map[string]json.RawMessage) bool {
	if value == nil {
		return false
	}
	for _, key := range []string{"url", "command", "transport", "headers", "auth", "type"} {
		if _, ok := value[key]; ok {
			return true
		}
	}
	return false
}

func inventoryUniqueYAMLKeys(node *yaml.Node) bool {
	seen := map[string]bool{}
	for i := 0; i < len(node.Content); i += 2 {
		key := node.Content[i]
		if key.Kind != yaml.ScalarNode || seen[key.Value] {
			return false
		}
		seen[key.Value] = true
	}
	return true
}
