package cmd

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"gopkg.in/yaml.v3"
)

const hostExecHarnessDeepSeek = "deepseek"

func validateDeepSeekHostExecProfile(p hostExecProfile) error {
	if len(p.Argv) > 0 || p.ForceWrites || p.AllowAllTools || len(p.AllowTools) > 0 || len(p.DenyTools) > 0 {
		return fmt.Errorf("DeepSeek requires runner-owned arguments and Preloop plugin approvals")
	}
	return nil
}
func deepSeekControlConfigPath() string {
	home := strings.TrimSpace(os.Getenv("DSH_HOME"))
	if home == "" {
		userHome, _ := os.UserHomeDir()
		home = filepath.Join(userHome, ".dsh")
	}
	return filepath.Join(home, "preloop.json")
}
func deepSeekApprovalInstalled() bool {
	doc, exists, err := loadJSONDocumentIfExists(deepSeekControlConfigPath())
	if err != nil || !exists {
		return false
	}
	preloop, _ := asObjectMap(doc["preloop"])
	control, _ := asObjectMap(preloop["control"])
	entry, err := os.Stat(harnessPluginEntry(AgentConfig{Name: "DeepSeek Harness"}))
	return err == nil && entry.Mode().IsRegular() && lookupString(control, "native_tool_approvals") == "on" && lookupString(control, "control_ws_url") != "" && lookupString(control, "bearer_token") != ""
}

// Verified published @deepseek-ai/dsh@0.2.0-rc.2 (headless JSON/stdin,
// tools.schemas and MCP readiness); also checked source at 1517a4d:
// https://github.com/deepseek-ai/deepseek-harness/blob/1517a4dbca0a2789ff1d41ba439600f8547d23fa/packages/bundle/headless/README.md
// The --patch overlay retains the shipped profile and native settings. Its
// explicit plugin insertion makes governance independent of profile registration.
func prepareDeepSeekHostExec(p hostExecProfile, job map[string]any, workspace, scratch, mcpToken string) ([]string, string, []string, []string, error) {
	if err := validateDeepSeekHostExecProfile(p); err != nil {
		return nil, "", nil, nil, err
	}
	if !deepSeekApprovalInstalled() {
		return nil, "", nil, nil, fmt.Errorf("DeepSeek Preloop approval plugin is not configured; onboard with --approvals")
	}
	doc, _, configErr := loadJSONDocumentIfExists(deepSeekControlConfigPath())
	if configErr != nil {
		return nil, "", nil, nil, configErr
	}
	preloop, _ := asObjectMap(doc["preloop"])
	if preloop["model"] != nil {
		return nil, "", nil, nil, fmt.Errorf("DeepSeek native host execution requires native provider routing; remove managed gateway model configuration")
	}
	prompt, err := claudeHostExecPrompt(job)
	if err != nil {
		return nil, "", nil, nil, err
	}
	nonceBytes := make([]byte, 32)
	if _, err := rand.Read(nonceBytes); err != nil {
		return nil, "", nil, nil, err
	}
	nonce := hex.EncodeToString(nonceBytes)
	entryPath := filepath.ToSlash(harnessPluginEntry(AgentConfig{Name: "DeepSeek Harness"}))
	if filepath.VolumeName(harnessPluginEntry(AgentConfig{Name: "DeepSeek Harness"})) != "" {
		entryPath = "/" + entryPath
	}
	entryURL := (&url.URL{Scheme: "file", Path: entryPath}).String()
	encodedURL, _ := json.Marshal(entryURL)
	wrapper := filepath.Join(scratch, "deepseek-governance.mjs")
	module := "import * as installed from " + string(encodedURL) + `;
export const name = 'preloop-host-governance';
export const inject = ['tools', 'agents'];
export async function apply(ctx, options) {
 // Observe only registrations made by this plugin, without modifying the
 // shared context while sibling plugins activate concurrently.
 const guards = new Set();
 const hooks = new Set();
 const track = (target, method, args, registrations) => {
  const dispose = Reflect.apply(method, target, args);
  if (typeof dispose !== 'function') throw new Error('Preloop governance registration has no disposer');
  const registration = {active: true};
  registrations.add(registration);
  return () => { registration.active = false; return dispose(); };
 };
 const tools = ctx.tools;
 const observedTools = new Proxy(tools, {get(target, key) {
  const value = Reflect.get(target, key, target);
  if (key === 'guard') return (...args) => {
   if (typeof args[0] !== 'function') throw new Error('Preloop governance guard missing');
   return track(target, value, args, guards);
  };
  return typeof value === 'function' ? value.bind(target) : value;
 }});
 const observedContext = new Proxy(ctx, {get(target, key) {
  if (key === 'tools') return observedTools;
  const value = Reflect.get(target, key, target);
  if (key === 'on') return (...args) => {
   if (args[0] === 'tools/pre-execute') {
    if (typeof args[1] !== 'function') throw new Error('Preloop governance hook missing');
    return track(target, value, args, hooks);
   }
   return Reflect.apply(value, target, args);
  };
  return typeof value === 'function' ? value.bind(target) : value;
 }});
 const governanceActive = () => [...guards].some(item => item.active) && [...hooks].some(item => item.active);
 await installed.apply(observedContext, options);
 if (!governanceActive()) throw new Error('Preloop governance tool guards did not register');
 process.stdout.write(JSON.stringify({type: 'preloop_governance_ready', nonce: process.env.PRELOOP_HOST_GOVERNANCE_NONCE}) + '\n');
}
`
	if mcpToken != "" {
		module = strings.Replace(module, " process.stdout.write", ` setImmediate(() => { void (async () => {
 const loader = ctx.get('loader');
 if (!loader || typeof loader.await !== 'function') return;
 await loader.await();
 if (!ctx.tools.schemas().some(tool => tool.name.startsWith('mcp__preloop-flow__'))) return;
 if (!governanceActive()) return;
 process.stdout.write`, 1)
		module = strings.Replace(module, " + '\\n');\n}", " + '\\n');\n })().catch(() => {}); });\n}", 1)
	}
	if err := writeFileAtomic(wrapper, []byte(module), 0600); err != nil {
		return nil, "", nil, nil, err
	}
	cleanupOnError := true
	defer func() {
		if cleanupOnError {
			_ = os.Remove(wrapper)
		}
	}()
	inserts := []any{map[string]any{"id": "preloop-host-control", "name": wrapper, "config": map[string]any{"configPath": deepSeekControlConfigPath()}}}
	// Native headless disables GUI authentication by default. Restore dsh's
	// own services so an existing local subscription/sign-in remains authoritative;
	// Preloop never reads or copies the native credential store.
	rows := []any{
		map[string]any{"id": "credentials", "disabled": false},
		map[string]any{"id": "authorization", "disabled": false},
		map[string]any{"id": "deepseek-account", "disabled": false},
		map[string]any{"id": "headless-runner", "config": map[string]any{"task": "-", "json": true}},
	}
	if model := jobModelIdentifier(job); model != "" {
		alias := p.ModelMap[model]
		provider, modelID, ok := strings.Cut(alias, "/")
		if !ok || provider == "" || modelID == "" {
			return nil, "", nil, nil, fmt.Errorf("DeepSeek model_map requires provider/model")
		}
		rows = append(rows, map[string]any{"id": "agent-default-model", "config": map[string]any{"provider": provider, "model": modelID}})
	}
	if mcpToken != "" {
		base, e := runnerControlPlaneURL()
		if e != nil {
			return nil, "", nil, nil, e
		}
		inserts = append(inserts, map[string]any{"id": "preloop-mcp-" + hostExecMCPServerName, "name": "@deepseek-ai/dsh-mcp-client", "config": map[string]any{"serverName": hostExecMCPServerName, "transport": "streamable-http", "url": strings.TrimRight(base, "/") + "/mcp/v1", "headers": map[string]string{"Authorization": "Bearer " + mcpToken}, "failOnStartupError": true}})
	}
	rows = append(rows, map[string]any{"insert": inserts})
	data, err := yaml.Marshal(rows)
	if err != nil {
		return nil, "", nil, nil, err
	}
	path := filepath.Join(scratch, "deepseek-run.patch.yml")
	if err = writeFileAtomic(path, data, 0600); err != nil {
		return nil, "", nil, nil, err
	}
	cleanupOnError = false
	return []string{"--profile", "headless", "--patch", path, "--json", "-"}, prompt, []string{"PRELOOP_HOST_GOVERNANCE_NONCE=" + nonce}, []string{path, wrapper}, nil
}

type deepSeekCapture struct {
	SessionID    string
	Finals       int
	Reason       string
	Failed       bool
	InputTokens  int64
	OutputTokens int64
}

func applyDeepSeekLine(c *deepSeekCapture, line string) {
	var e struct {
		Type      string `json:"type"`
		SessionID string `json:"sessionId"`
		Phase     string `json:"phase"`
		Reason    string `json:"reason"`
		Usage     struct {
			Input  int64 `json:"inputTokens"`
			Output int64 `json:"outputTokens"`
		} `json:"usage"`
	}
	if json.Unmarshal([]byte(line), &e) != nil {
		return
	}
	switch e.Type {
	case "session":
		if strings.HasPrefix(e.SessionID, "session-") && len(e.SessionID) <= 256 && !strings.ContainsAny(e.SessionID, " /\\\r\n\t") {
			if c.SessionID != "" {
				c.Failed = true
			}
			c.SessionID = e.SessionID
		}
	case "error":
		c.Failed = true
	case "final":
		c.Finals++
	case "status":
		switch e.Phase {
		case "turn_start":
			c.Reason = ""
		case "turn_end":
			c.Reason = e.Reason
			if e.Reason != "completed" {
				c.Failed = true
			}
		case "step_end":
			if e.Usage.Input >= 0 {
				c.InputTokens += e.Usage.Input
			}
			if e.Usage.Output >= 0 {
				c.OutputTokens += e.Usage.Output
			}
		}
	}
}
func deepSeekRunnerResult(c deepSeekCapture) (map[string]any, error) {
	if c.SessionID == "" || c.Finals != 1 || c.Failed || c.Reason != "completed" {
		return nil, fmt.Errorf("DeepSeek exited without a successful structured completion")
	}
	return map[string]any{"status": "success", "harness": hostExecHarnessDeepSeek, "session_id": c.SessionID, "usage": map[string]any{"input_tokens": c.InputTokens, "output_tokens": c.OutputTokens}}, nil
}
