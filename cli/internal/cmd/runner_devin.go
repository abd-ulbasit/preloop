package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// Devin CLI 3000.3+ keeps MCP in dedicated mcp_config files. Model calls use
// the runner user's Cognition login; native tools use Preloop command hooks.
func devinConfigPath(name string) (string, error) {
	root := os.Getenv("XDG_CONFIG_HOME")
	if runtime.GOOS == "windows" {
		root = os.Getenv("APPDATA")
	}
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		root = filepath.Join(home, ".config")
	}
	return filepath.Join(root, "devin", name), nil
}

func devinApprovalHooksInstalled() bool {
	path, err := devinConfigPath("config.json")
	if err != nil {
		return false
	}
	doc, err := loadJSONDocumentOrEmpty(path)
	if err != nil {
		return false
	}
	hooks, _ := doc["hooks"].(map[string]interface{})
	for _, event := range []string{"PreToolUse", "PermissionRequest"} {
		found := false
		for _, raw := range asArrayValue(hooks[event]) {
			entry, _ := asObjectMap(raw)
			matcher, _ := entry["matcher"].(string)
			if matcher != "" && matcher != ".*" {
				continue
			}
			for _, rawHook := range asArrayValue(entry["hooks"]) {
				hook, _ := asObjectMap(rawHook)
				command, _ := hook["command"].(string)
				if hook["type"] == "command" && strings.Contains(command, "agents permission-hook --source devin_cli") {
					found = true
				}
			}
		}
		if !found {
			return false
		}
	}
	return true
}

func validateDevinHostExecProfile(profile hostExecProfile) error {
	if profile.ForceWrites || profile.AllowAllTools || len(profile.AllowTools) > 0 || len(profile.DenyTools) > 0 {
		return fmt.Errorf("Devin tools require Preloop approval hooks; tool grants and force_writes are unavailable")
	}
	// Devin supports subcommands and config/permission overrides. Keep local
	// profiles declarative: the runner owns every argument on this harness.
	if len(profile.Argv) > 0 {
		return fmt.Errorf("Devin profile argv is unavailable; use model_map and Preloop approvals")
	}
	return nil
}

func buildDevinHostExecArgs(profile hostExecProfile, job map[string]any, path, resume string) ([]string, error) {
	if err := validateDevinHostExecProfile(profile); err != nil {
		return nil, err
	}
	prompt, err := jobPromptText(job)
	if err != nil {
		return nil, err
	}
	if strings.TrimSpace(prompt) == "" || strings.ContainsRune(prompt, 0) {
		return nil, fmt.Errorf("Devin requires a nonempty prompt without NUL")
	}
	args := []string{"--permission-mode", "auto", "--respect-workspace-trust", "false", "--export", path, "-p"}
	if model := jobModelIdentifier(job); model != "" {
		alias := profile.ModelMap[model]
		if alias == "" {
			return nil, fmt.Errorf("model not in local model_map")
		}
		args = append(args, "--model", alias)
	}
	if resume != "" {
		if !copilotSessionIDRe.MatchString(resume) {
			return nil, fmt.Errorf("invalid Devin session id")
		}
		args = append(args, "--resume", resume)
	}
	return append(args, "--", prompt), nil
}

func newDevinExportPath() (string, error) {
	file, err := os.CreateTemp("", "preloop-devin-*.json")
	if err != nil {
		return "", err
	}
	name := file.Name()
	if err := file.Close(); err != nil {
		_ = os.Remove(name)
		return "", err
	}
	return name, nil
}

type devinExport struct {
	SchemaVersion string `json:"schema_version"`
	SessionID     string `json:"session_id"`
	Agent         struct {
		Name  string `json:"name"`
		Model string `json:"model_name"`
	} `json:"agent"`
	Steps []struct {
		Source    string            `json:"source"`
		Message   json.RawMessage   `json:"message"`
		ToolCalls []json.RawMessage `json:"tool_calls"`
	} `json:"steps"`
}

// An exit code alone is insufficient: require a fresh bounded ATIF export
// ending with the agent's final response, rather than an unfinished tool call.
func readDevinExport(path string) (devinExport, error) {
	var export devinExport
	file, err := os.Open(path)
	if err != nil {
		return export, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return export, err
	}
	if !info.Mode().IsRegular() || info.Size() > 16*1024*1024 {
		return export, fmt.Errorf("invalid or oversized Devin export")
	}
	raw, err := io.ReadAll(io.LimitReader(file, 16*1024*1024+1))
	if err != nil {
		return export, err
	}
	if err := json.Unmarshal(raw, &export); err != nil {
		return export, fmt.Errorf("invalid Devin ATIF export: %w", err)
	}
	if !strings.HasPrefix(export.SchemaVersion, "ATIF-v1.") || !copilotSessionIDRe.MatchString(export.SessionID) || len(export.Steps) == 0 {
		return export, fmt.Errorf("Devin exited without a valid ATIF result")
	}
	last := export.Steps[len(export.Steps)-1]
	if last.Source != "agent" || len(last.ToolCalls) > 0 || len(last.Message) == 0 || string(last.Message) == "null" {
		return export, fmt.Errorf("Devin exited without a final agent response")
	}
	return export, nil
}
func (e devinExport) result() map[string]any {
	result := map[string]any{"status": "success", "harness": hostExecHarnessDevin, "session_id": e.SessionID}
	if hostExecModelRe.MatchString(e.Agent.Model) {
		result["model"] = e.Agent.Model
		noteHarnessRunSucceeded(hostExecHarnessDevin, e.Agent.Model)
	}
	return result
}

// Status output can contain account information; retain only the verdict.
func probeDevinStatus(executable string) (string, []string) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, "auth", "status")
	cmd.Env = hostExecProbeEnv(executable)
	out := &boundedTail{max: 8192}
	cmd.Stdout = out
	cmd.Stderr = out
	err := cmd.Run()
	text := strings.ToLower(out.String())
	login := harnessLoginUnknown
	if strings.Contains(text, "not authenticated") || strings.Contains(text, "not logged in") || strings.Contains(text, "logged out") {
		login = harnessLoginSignedOut
	} else if err == nil && (strings.Contains(text, "authenticated") || strings.Contains(text, "logged in")) {
		login = harnessLoginSignedIn
	}
	return login, probeDevinModels(executable)
}
func buildDevinEntry(d harnessInventoryDeps) (harnessInventoryEntry, bool) {
	if d.Resolve == nil {
		return harnessInventoryEntry{}, false
	}
	executable, err := d.Resolve("devin")
	if err != nil {
		return harnessInventoryEntry{}, false
	}
	entry := harnessInventoryEntry{Harness: hostExecHarnessDevin, DisplayName: "Devin CLI", LoginState: harnessLoginUnknown, LoginSource: "unknown", Governance: "ungoverned", SupportLevel: harnessSupportFlowsSessions, SessionMode: "resume", Billing: "seat", Capabilities: hostExecCapabilitiesFor(hostExecHarnessDevin)}
	if d.Version != nil {
		entry.Version = d.Version(executable)
	}
	var models []string
	if d.DevinStatus != nil {
		entry.LoginState, models = d.DevinStatus(executable)
		if entry.LoginState != harnessLoginUnknown {
			entry.LoginSource = "cli_status"
		}
	}
	signedOut, observed := d.Hints.snapshot(hostExecHarnessDevin)
	if signedOut {
		entry.LoginState = harnessLoginSignedOut
	}
	seen := map[string]struct{}{}
	entry.Models = appendModels(nil, seen, harnessModelSourceProbed, models...)
	entry.Models = appendModels(entry.Models, seen, harnessModelSourceObserved, observed...)
	if devinApprovalHooksInstalled() {
		entry.Governance = "governed"
	}
	if name := generatedProfileFor(hostExecHarnessDevin, d); name != "" {
		entry.GeneratedProfile = &name
	}
	return entry, true
}

func parseDevinMCP(path string) (map[string]MCPDef, error) {
	doc, err := loadDevinDocument(path)
	if err != nil {
		return nil, err
	}
	return parseServerMapFromDocument(doc), nil
}

func parseDevinModels(raw []byte) []string {
	var listing struct {
		Families []struct {
			Variants []struct {
				ModelUID string `json:"model_uid"`
			} `json:"variants"`
		} `json:"families"`
	}
	if json.Unmarshal(raw, &listing) != nil {
		return nil
	}
	models := []string{}
	seen := map[string]bool{}
	for _, family := range listing.Families {
		for _, variant := range family.Variants {
			id := variant.ModelUID
			if len(models) < maxHarnessModels && hostExecModelRe.MatchString(id) && !seen[id] {
				models = append(models, id)
				seen[id] = true
			}
		}
	}
	return models
}

func probeDevinModels(executable string) []string {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, executable, "models", "list", "--format", "json")
	cmd.Env = hostExecProbeEnv(executable)
	out := &boundedTail{max: 256 * 1024}
	cmd.Stdout = out
	if cmd.Run() != nil {
		return nil
	}
	return parseDevinModels([]byte(out.String()))
}

// Devin documents JSONC line and block comments. Preserve string contents,
// including URL slashes, and reject unfinished comments rather than truncating.
func loadDevinDocument(path string) (map[string]interface{}, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	cleaned := append([]byte{}, raw...)
	quoted, escaped := false, false
	for i := 0; i < len(raw); i++ {
		if quoted {
			if escaped {
				escaped = false
			} else if raw[i] == '\\' {
				escaped = true
			} else if raw[i] == '"' {
				quoted = false
			}
			continue
		}
		if raw[i] == '"' {
			quoted = true
			continue
		}
		if raw[i] != '/' || i+1 >= len(raw) {
			continue
		}
		if raw[i+1] == '/' {
			for ; i < len(raw) && raw[i] != '\n'; i++ {
				cleaned[i] = ' '
			}
			continue
		}
		if raw[i+1] == '*' {
			cleaned[i] = ' '
			i++
			cleaned[i] = ' '
			closed := false
			for i++; i < len(raw); i++ {
				if raw[i] == '*' && i+1 < len(raw) && raw[i+1] == '/' {
					cleaned[i] = ' '
					i++
					cleaned[i] = ' '
					closed = true
					break
				}
				if raw[i] != '\n' {
					cleaned[i] = ' '
				}
			}
			if !closed {
				return nil, fmt.Errorf("unfinished Devin config comment")
			}
		}
	}
	var doc map[string]interface{}
	if err := json.Unmarshal(cleaned, &doc); err != nil {
		return nil, err
	}
	if doc == nil {
		return nil, fmt.Errorf("Devin config must be an object")
	}
	return doc, nil
}
