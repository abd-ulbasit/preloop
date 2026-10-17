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
)

const hostExecHarnessOpenCode = "opencode"

func validateOpenCodeHostExecProfile(p hostExecProfile) error {
	if len(p.Argv) > 0 || p.ForceWrites || p.AllowAllTools || len(p.AllowTools) > 0 || len(p.DenyTools) > 0 {
		return fmt.Errorf("OpenCode requires runner-owned arguments and Preloop plugin approvals")
	}
	return nil
}

func openCodeApprovalInstalled() bool {
	doc, exists, err := loadOpenCodeUserConfig()
	if err != nil || !exists || !openCodePluginRegistered(doc) {
		return false
	}
	control, ok := openCodeControlBlock(doc)
	return ok && lookupString(control, "native_tool_approvals") == "on" && lookupString(control, "control_ws_url") != "" && lookupString(control, "bearer_token") != ""
}

// OpenCode v1.18.32: run JSON is a projection of completed parts, not SSE.
// https://github.com/anomalyco/opencode/blob/v1.18.32/packages/opencode/src/cli/cmd/run.ts
func prepareOpenCodeHostExec(p hostExecProfile, job map[string]any, workspace, scratch, mcpToken string) ([]string, string, []string, []string, error) {
	if err := validateOpenCodeHostExecProfile(p); err != nil {
		return nil, "", nil, nil, err
	}
	if !openCodeApprovalInstalled() {
		return nil, "", nil, nil, fmt.Errorf("OpenCode Preloop approval plugin is not configured; onboard with --approvals")
	}
	prompt, err := claudeHostExecPrompt(job)
	if err != nil {
		return nil, "", nil, nil, err
	}
	args := []string{"run", "--format", "json", "--dir", workspace}
	if model := jobModelIdentifier(job); model != "" {
		alias := p.ModelMap[model]
		if provider, model, ok := strings.Cut(alias, "/"); !ok || provider == "" || model == "" {
			return nil, "", nil, nil, fmt.Errorf("OpenCode model_map requires provider/model")
		}
		args = append(args, "--model", alias)
	}
	// Config files merge with native user configuration, retaining login and plugins.
	nonceBytes := make([]byte, 32)
	if _, err := rand.Read(nonceBytes); err != nil {
		return nil, "", nil, nil, err
	}
	nonce := hex.EncodeToString(nonceBytes)
	wrapper := filepath.Join(scratch, "opencode-governance.mjs")
	module := `import { createRequire } from 'node:module';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { pathToFileURL } from 'node:url';
export default async function preloopHostGovernance(ctx, options) {
 const cache = join(process.env.XDG_CACHE_HOME || join(homedir(), '.cache'), 'opencode');
 const root = join(cache, 'packages', '@preloop-ai/opencode-plugin@latest');
 const require = createRequire(join(root, 'package.json'));
 const installed = await import(pathToFileURL(require.resolve('@preloop-ai/opencode-plugin')).href);
 const apply = installed.PreloopPlugin || installed.default;
 if (typeof apply !== 'function') throw new Error('Preloop governance plugin missing');
 const hooks = await apply(ctx, options);
 if (typeof hooks?.['tool.execute.before'] !== 'function') throw new Error('Preloop governance tool gate missing');
 process.stdout.write(JSON.stringify({type:'preloop_governance_ready',nonce:process.env.PRELOOP_HOST_GOVERNANCE_NONCE}) + '\n');
 return hooks;
}
`
	if mcpToken != "" {
		module = strings.Replace(module, " process.stdout.write", ` setImmediate(() => { void (async () => {
 const response = await ctx.client.mcp.status();
 if (response.error || response.data?.['preloop-flow']?.status !== 'connected') return;
 process.stdout.write`, 1)
		module = strings.Replace(module, " + '\\n');\n return hooks;", " + '\\n');\n })().catch(() => {}); });\n return hooks;", 1)
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
	wrapperPath := filepath.ToSlash(wrapper)
	if filepath.VolumeName(wrapper) != "" {
		wrapperPath = "/" + wrapperPath
	}
	wrapperURL := (&url.URL{Scheme: "file", Path: wrapperPath}).String()
	doc := map[string]any{"plugin": []string{openCodePluginPackageName, wrapperURL}}
	if mcpToken != "" {
		base, e := runnerControlPlaneURL()
		if e != nil {
			return nil, "", nil, nil, e
		}
		doc["mcp"] = map[string]any{hostExecMCPServerName: map[string]any{"type": "remote", "url": strings.TrimRight(base, "/") + "/mcp/v1", "headers": map[string]string{"Authorization": "Bearer " + mcpToken}}}
	}
	path := filepath.Join(scratch, "opencode-run.json")
	data, err := json.Marshal(doc)
	if err != nil {
		return nil, "", nil, nil, err
	}
	if err = writeFileAtomic(path, data, 0600); err != nil {
		return nil, "", nil, nil, err
	}
	cleanupOnError = false
	return args, prompt, []string{"OPENCODE_CONFIG=" + path, "OPENCODE_CONFIG_CONTENT=", "OPENCODE_CONFIG_DIR=", "PRELOOP_HOST_GOVERNANCE_NONCE=" + nonce, "PRELOOP_OPENCODE_CONTROL_CONFIG=" + openCodeUserConfigPath()}, []string{path, wrapper}, nil
}

type openCodeCapture struct {
	SessionID    string
	LastReason   string
	Steps        int
	Failed       bool
	InputTokens  int64
	OutputTokens int64
}

func applyOpenCodeLine(c *openCodeCapture, line string) {
	var e struct {
		Type      string `json:"type"`
		SessionID string `json:"sessionID"`
		Part      struct {
			Reason string `json:"reason"`
			Tokens struct {
				Input  int64 `json:"input"`
				Output int64 `json:"output"`
			} `json:"tokens"`
		} `json:"part"`
	}
	if json.Unmarshal([]byte(line), &e) != nil {
		return
	}
	if strings.HasPrefix(e.SessionID, "ses_") && len(e.SessionID) <= 256 && !strings.ContainsAny(e.SessionID, " /\\\r\n\t") {
		if c.SessionID != "" && c.SessionID != e.SessionID {
			c.Failed = true
		}
		c.SessionID = e.SessionID
	}
	switch e.Type {
	case "error":
		c.Failed = true
	case "step_start":
		c.LastReason = ""
	case "step_finish":
		c.Steps++
		c.LastReason = e.Part.Reason
		if e.Part.Tokens.Input >= 0 {
			c.InputTokens += e.Part.Tokens.Input
		}
		if e.Part.Tokens.Output >= 0 {
			c.OutputTokens += e.Part.Tokens.Output
		}
	}
}
func openCodeRunnerResult(c openCodeCapture) (map[string]any, error) {
	if c.SessionID == "" || c.Steps == 0 || c.LastReason != "stop" || c.Failed {
		return nil, fmt.Errorf("OpenCode exited without a successful structured completion")
	}
	return map[string]any{"status": "success", "harness": hostExecHarnessOpenCode, "session_id": c.SessionID, "usage": map[string]any{"input_tokens": c.InputTokens, "output_tokens": c.OutputTokens}}, nil
}
