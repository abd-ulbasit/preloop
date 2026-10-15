package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	toml "github.com/pelletier/go-toml/v2"
	json5 "github.com/yosuke-furukawa/json5/encoding/json5"
	"gopkg.in/yaml.v3"
)

// Configured declarations, not observed calls. Managed means a recognizable
// Preloop entry, not a trusted account binding or proof that it can execute.
type inventoryMCPRoutes struct {
	Managed      int    `json:"managed"`
	DirectRemote int    `json:"direct_remote"`
	DirectStdio  int    `json:"direct_stdio"`
	Unknown      int    `json:"unknown"`
	Completeness string `json:"completeness"`
}

func (r *inventoryMCPRoutes) add(other inventoryMCPRoutes) {
	r.Managed += other.Managed
	r.DirectRemote += other.DirectRemote
	r.DirectStdio += other.DirectStdio
	r.Unknown += other.Unknown
	if other.Completeness == "partial" {
		r.Completeness = "partial"
	}
}

func inventoryHTTPURL(value string) *url.URL {
	parsed, err := url.Parse(strings.TrimSpace(value))
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Hostname() == "" {
		return nil
	}
	return parsed
}

func inventoryClassifyRoute(name string, server MCPDef, valid bool) inventoryMCPRoutes {
	r := inventoryMCPRoutes{Completeness: "complete"}
	remote := inventoryHTTPURL(server.URL)
	command := strings.TrimSpace(server.Command)
	transport := strings.ToLower(strings.TrimSpace(server.Transport))
	knownTransport := transport == "" || transport == "stdio" || transport == "http" || transport == "http-streaming" || transport == "sse" || transport == "streamable-http" || transport == "streamable_http" || transport == "remote" || transport == "local"
	// The adapter reserves this name. A stale/malformed owned entry must not
	// count as a working managed route or disappear into the direct totals.
	owned := strings.EqualFold(strings.TrimSpace(name), "preloop")
	if remote != nil {
		host := strings.ToLower(remote.Hostname())
		owned = owned || host == "preloop.ai" || strings.HasSuffix(host, ".preloop.ai")
	}
	if !valid || !knownTransport {
		r.Unknown++
		return r
	}
	if owned {
		target := remote
		bridge := false
		// Recognize only the bridge shape our Desktop adapter writes. Arbitrary
		// command arguments/env are never searched for URLs or sent to inventory.
		if target == nil && command != "" && (filepath.Base(command) == "npx" || filepath.Base(command) == "npx.cmd") && len(server.Args) >= 2 {
			for i, arg := range server.Args[:len(server.Args)-1] {
				if arg == "mcp-remote" {
					target = inventoryHTTPURL(server.Args[i+1])
					bridge = target != nil
					break
				}
			}
		}
		if target != nil && strings.TrimRight(target.Path, "/") == "/mcp/v1" && ((command == "" && transport != "stdio" && transport != "local") || (bridge && server.URL == "" && (transport == "" || transport == "stdio" || transport == "local"))) {
			r.Managed++
		} else {
			r.Unknown++
		}
		return r
	}
	switch {
	case remote != nil && command == "" && transport != "stdio" && transport != "local":
		r.DirectRemote++
	case command != "" && server.URL == "" && (transport == "" || transport == "stdio" || transport == "local"):
		r.DirectStdio++
	default:
		r.Unknown++
	}
	return r
}

func inventoryRoutesForServers(servers map[string]MCPDef) inventoryMCPRoutes {
	result := inventoryMCPRoutes{Completeness: "complete"}
	for name, server := range servers {
		result.add(inventoryClassifyRoute(name, server, true))
	}
	return result
}

// Select only routing fields. Names and values are used locally for bounded
// classification and never enter the returned DTO. No credential resolution.
func inventoryRouteFromRaw(name string, raw json.RawMessage, decode func([]byte, any) error) inventoryMCPRoutes {
	fields, ok := inventoryRawObject(decode, raw)
	if !ok {
		return inventoryClassifyRoute(name, MCPDef{}, false)
	}
	def := MCPDef{}
	valid := true
	var kind, transport string
	for _, field := range []struct {
		key string
		dst *string
	}{{"url", &def.URL}, {"command", &def.Command}, {"type", &kind}, {"transport", &transport}} {
		key, dst := field.key, field.dst
		if value, exists := fields[key]; exists {
			value = json.RawMessage(strings.TrimSpace(string(value)))
			if string(value) == "null" {
				valid = false
				continue
			}
			// OpenCode's local command is an argv array.
			if key == "command" && len(value) > 0 && value[0] == '[' {
				var args []string
				if decode(value, &args) != nil || len(args) == 0 {
					valid = false
				} else {
					def.Command = args[0]
					def.Args = args[1:]
				}
			} else if decode(value, dst) != nil {
				valid = false
			}
		}
	}
	if value, exists := fields["args"]; exists && decode(value, &def.Args) != nil {
		valid = false
	}
	for _, key := range []string{"serverUrl", "httpUrl"} {
		if raw, exists := fields[key]; exists {
			var value string
			if decode(raw, &value) != nil || value == "" || (def.URL != "" && def.URL != value) {
				valid = false
			} else {
				def.URL = value
			}
		}
	}
	for _, field := range []struct {
		key      string
		disabled bool
	}{{"enabled", false}, {"disabled", true}} {
		if raw, exists := fields[field.key]; exists {
			var value bool
			if decode(raw, &value) != nil || value == field.disabled {
				valid = false
			}
		}
	}
	if kind != "" && transport != "" && kind != transport {
		valid = false
	}
	def.Transport = kind
	if transport != "" {
		def.Transport = transport
	}
	return inventoryClassifyRoute(name, def, valid)
}

func inventoryConfigRoutes(appName, path string, data []byte) (inventoryMCPRoutes, error) {
	result := inventoryMCPRoutes{Completeness: "complete"}
	// Reuse the collector's syntax/shape validation and container selection.
	if _, err := inventoryMCPServerCount(appName, path, data); err != nil {
		result.Completeness = "partial"
		return result, err
	}
	decode := json.Unmarshal
	var servers map[string]json.RawMessage
	switch strings.ToLower(filepath.Ext(path)) {
	case ".toml":
		// TOML decodes only these allowlisted fields, skipping env/auth/headers.
		var doc struct {
			Servers map[string]struct {
				URL       any `toml:"url"`
				Command   any `toml:"command"`
				Args      any `toml:"args"`
				Type      any `toml:"type"`
				Transport any `toml:"transport"`
				Enabled   any `toml:"enabled"`
				Disabled  any `toml:"disabled"`
			} `toml:"mcp_servers"`
		}
		if toml.Unmarshal(data, &doc) != nil {
			return inventoryMCPRoutes{Completeness: "partial"}, errInventoryConfig
		}
		servers = map[string]json.RawMessage{}
		for name, s := range doc.Servers {
			fields := map[string]any{}
			for key, v := range map[string]any{"url": s.URL, "command": s.Command, "args": s.Args, "type": s.Type, "transport": s.Transport, "enabled": s.Enabled, "disabled": s.Disabled} {
				if v != nil {
					fields[key] = v
				}
			}
			servers[name], _ = json.Marshal(fields)
		}
	case ".yaml", ".yml":
		var doc yaml.Node
		if yaml.Unmarshal(data, &doc) != nil {
			return inventoryMCPRoutes{Completeness: "partial"}, errInventoryConfig
		}
		servers = map[string]json.RawMessage{}
		root := doc.Content[0]
		for i := 0; i < len(root.Content); i += 2 {
			if root.Content[i].Value == "mcp_servers" {
				entries := root.Content[i+1]
				for j := 0; j < len(entries.Content); j += 2 {
					entry := entries.Content[j+1]
					fields := map[string]any{}
					for k := 0; k < len(entry.Content); k += 2 {
						key := entry.Content[k].Value
						if key == "url" || key == "command" || key == "args" || key == "type" || key == "transport" || key == "enabled" || key == "disabled" {
							var v any
							if entry.Content[k+1].Decode(&v) != nil {
								fields[key] = false
							} else {
								fields[key] = v
							}
						}
					}
					servers[entries.Content[j].Value], _ = json.Marshal(fields)
				}
			}
		}
	default:
		if appName == "OpenClaw" {
			decode = json5.Unmarshal
		}
		doc, _ := inventoryRawObject(decode, data)
		for _, key := range []string{"mcpServers", "servers", "mcp_servers"} {
			if raw, exists := doc[key]; exists {
				if _, ok := inventoryRawObject(decode, raw); !ok {
					result.Completeness = "partial"
				}
			}
		}
		if raw, exists := doc["mcp"]; exists {
			if mcp, ok := inventoryRawObject(decode, raw); !ok {
				result.Completeness = "partial"
			} else if nested, exists := mcp["servers"]; exists {
				if _, ok := inventoryRawObject(decode, nested); !ok {
					result.Completeness = "partial"
				}
			}
		}
		servers = inventoryLookupMCPContainer(doc, decode)
	}
	for name, raw := range servers {
		result.add(inventoryRouteFromRaw(name, raw, decode))
	}
	return result, nil
}

func inventoryRoutesForDocument(doc map[string]interface{}) inventoryMCPRoutes {
	result := inventoryMCPRoutes{Completeness: "complete"}
	for name, value := range lookupMCPServerContainer(doc) {
		// Use the same narrow classifier for plans; this does not change the doc.
		fields, ok := asObjectMap(value)
		if !ok {
			result.add(inventoryClassifyRoute(name, MCPDef{}, false))
			continue
		}
		selected := map[string]interface{}{}
		for _, key := range []string{"url", "command", "args", "transport", "type", "serverUrl", "httpUrl", "enabled", "disabled"} {
			if value, exists := fields[key]; exists {
				selected[key] = value
			}
		}
		raw, err := json.Marshal(selected)
		if err != nil {
			result.add(inventoryClassifyRoute(name, MCPDef{}, false))
		} else {
			result.add(inventoryRouteFromRaw(name, raw, json.Unmarshal))
		}
	}
	return result
}

func inventoryRouteSummary(routes inventoryMCPRoutes) string {
	return fmt.Sprintf("Configured MCP routes: managed=%d, direct remote=%d, direct local stdio=%d, unknown=%d (%s). Existing direct entries are preserved; they bypass Preloop MCP policy. Only calls through the managed entry are checked. Configured routes do not establish observed usage.", routes.Managed, routes.DirectRemote, routes.DirectStdio, routes.Unknown, routes.Completeness)
}

// The legacy discovery parser can drop malformed entries. Cache the shared
// classifier's raw-file facts so its safe DTO retains unknown declarations.
// Serialization itself remains pure and never opens a path.
func inventoryReadConfigRoutes(appName, path string) *inventoryMCPRoutes {
	result := inventoryMCPRoutes{Completeness: "partial"}
	file, err := os.Open(path)
	if os.IsNotExist(err) {
		result.Completeness = "complete"
		return &result
	}
	if err != nil {
		return &result
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Size() > inventoryConfigLimit {
		return &result
	}
	data, err := io.ReadAll(io.LimitReader(file, inventoryConfigLimit+1))
	if err != nil || len(data) > inventoryConfigLimit {
		return &result
	}
	result, err = inventoryConfigRoutes(appName, path, data)
	if err != nil {
		result.Completeness = "partial"
	}
	return &result
}

func inventoryDiscoveryRoutes(agent AgentConfig) inventoryMCPRoutes {
	if agent.inventoryRoutes != nil {
		return *agent.inventoryRoutes
	}
	result := inventoryRoutesForServers(agent.MCPServers)
	// A synthesized/legacy AgentConfig has no complete raw-container facts.
	result.Completeness = "partial"
	return result
}
