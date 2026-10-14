package cmd

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"sync"
	"time"
)

// claudeSessionAdapter runs Claude Code in per-turn resume mode (issue
// #1670), verified against Claude Code 2.1.296 `claude --help`:
//
//	turn 1: claude -p --output-format stream-json --verbose [--model <alias>]
//	        --session-id <uuid>          (prompt on stdin)
//	turn N: the same with --resume <uuid>
//
// The uuid is minted by the runner, never by the server, so a server cannot
// point a session at an unrelated Claude conversation on this host. The
// binary is the user's own installed claude, the environment is the
// host-exec allowlist, the runner changes no Claude settings or credentials
// (Claude Code itself keeps the transcript under ~/.claude/projects, which is
// what --resume reads), and the Preloop
// PreToolUse approval hook must be installed: every tool call goes through
// Preloop approvals. Token usage is reported from each turn's result event.
type claudeSessionAdapter struct {
	loadProfile           func() (hostExecProfile, error)
	approvalHookInstalled func() bool
	resolveCommand        func(executable string) (string, []string, error)
	environ               func() []string
	newID                 func() (string, error)
}

func newClaudeSessionAdapter() *claudeSessionAdapter {
	return &claudeSessionAdapter{
		loadProfile:           claudeSessionProfile,
		approvalHookInstalled: claudeApprovalHookInstalled,
		resolveCommand:        resolveHostExecCommand,
		environ:               os.Environ,
		newID:                 newSessionUUID,
	}
}

func (a *claudeSessionAdapter) Harness() string { return hostExecHarnessClaude }

// Mode is the contract A session_mode for this adapter.
func (a *claudeSessionAdapter) Mode() string { return "resume" }

func (a *claudeSessionAdapter) Stop(*runnerRemoteSession) error { return nil }

// claudeSessionProfile is the host profile named "claude" when the profile
// file has one, else a plain profile that runs `claude` from PATH.
func claudeSessionProfile() (hostExecProfile, error) {
	profiles, err := loadHostExecProfiles()
	if err != nil {
		return hostExecProfile{}, err
	}
	for _, profile := range profiles {
		if strings.EqualFold(profile.Name, "claude") {
			if hostExecProfileHarness(profile) != hostExecHarnessClaude {
				return hostExecProfile{}, fmt.Errorf("host profile %q does not run the claude binary", profile.Name)
			}
			return profile, nil
		}
	}
	return hostExecProfile{Name: "claude", Executable: "claude"}, nil
}

func (a *claudeSessionAdapter) profile() (hostExecProfile, error) {
	profile, err := a.loadProfile()
	if err != nil {
		return hostExecProfile{}, err
	}
	if err := validateClaudeHostExecProfile(profile); err != nil {
		return hostExecProfile{}, fmt.Errorf("host profile %q: %w", profile.Name, err)
	}
	return profile, nil
}

// Start refuses unless claude resolves and the Preloop approval hook is
// installed for this user, then mints the Claude session uuid.
func (a *claudeSessionAdapter) Start(s *runnerRemoteSession) error {
	profile, err := a.profile()
	if err != nil {
		return err
	}
	if _, _, err := a.resolveCommand(profile.Executable); err != nil {
		return fmt.Errorf("claude_not_found: %w", err)
	}
	if !a.approvalHookInstalled() {
		return fmt.Errorf("claude_approval_hook_missing: remote sessions require the Preloop PreToolUse approval hook; run `preloop agents onboard \"Claude Code\" --approvals` as the runner user")
	}
	id, err := a.newID()
	if err != nil {
		return err
	}
	s.HarnessSessionID = id
	return nil
}

// claudeSessionArgs renders one turn with the flow builder, then names the
// session: --session-id on the first turn, --resume afterwards.
func claudeSessionArgs(profile hostExecProfile, turn runnerSessionTurnSpec) ([]string, error) {
	if !uuidRe.MatchString(turn.HarnessSessionID) {
		return nil, fmt.Errorf("invalid harness session id")
	}
	job := map[string]any{"prompt": turn.Text}
	if model := strings.TrimSpace(turn.Model); model != "" && model != "auto" {
		if !hostExecModelRe.MatchString(model) {
			return nil, fmt.Errorf("model_not_available: invalid model id")
		}
		models := map[string]string{}
		for key, value := range profile.ModelMap {
			models[key] = value
		}
		if models[model] == "" {
			models[model] = model
		}
		profile.ModelMap = models
		job["model_identifier"] = model
	}
	id := strings.ToLower(turn.HarnessSessionID)
	if turn.Resume {
		return buildClaudeHostExecArgs(profile, job, "", id)
	}
	args, err := buildClaudeHostExecArgs(profile, job, "", "")
	if err != nil {
		return nil, err
	}
	return append(args, "--session-id", id), nil
}

func (a *claudeSessionAdapter) Turn(ctx context.Context, turn runnerSessionTurnSpec, emit func(string, map[string]any)) runnerSessionTurnResult {
	fail := func(code string, err error) runnerSessionTurnResult {
		return runnerSessionTurnResult{Status: "error", ErrorCode: code, Error: err.Error()}
	}
	if turn.Dir == "" {
		return fail(rejectWorkspaceNotAllowed, fmt.Errorf("no workspace"))
	}
	profile, err := a.profile()
	if err != nil {
		return fail("harness_unavailable", err)
	}
	if !a.approvalHookInstalled() {
		return fail("claude_approval_hook_missing", fmt.Errorf("the Preloop approval hook is no longer installed"))
	}
	args, err := claudeSessionArgs(profile, turn)
	if err != nil {
		return fail("invalid_turn", err)
	}
	bin, prefix, err := a.resolveCommand(profile.Executable)
	if err != nil {
		return fail("harness_unavailable", err)
	}
	args = append(append([]string{}, prefix...), args...)
	if err := hostExecCommandLineError(runtime.GOOS, bin, args); err != nil {
		return fail("invalid_turn", err)
	}
	cmd := exec.Command(bin, args...)
	cmd.Dir = turn.Dir
	cmd.Env = hostExecPrependPath(runtime.GOOS, hostExecChildEnv(runtime.GOOS, hostExecHarnessClaude, profile, a.environ()), hostExecBinaryDirs(bin, profile.Executable)...)
	cmd.Stdin = strings.NewReader(turn.Text)
	cmd.SysProcAttr = hostExecSysProcAttr()
	cmd.WaitDelay = 250 * time.Millisecond
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fail("harness_unavailable", err)
	}
	stderr := &boundedTail{max: claudeMaxErrorBytes * 4}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		return fail("harness_unavailable", err)
	}
	finished := make(chan struct{})
	var killed bool
	var killMu sync.Mutex
	go func() {
		select {
		case <-ctx.Done():
			killMu.Lock()
			killed = true
			killMu.Unlock()
			killRunnerJobProcess(cmd)
		case <-finished:
		}
	}()
	capture := claudeCapture{}
	reader := bufio.NewReaderSize(stdout, 64*1024)
	for {
		line, readErr := readBoundedLine(reader, 4*1024*1024)
		if line != "" {
			for _, event := range claudeSessionEvents(&capture, line) {
				emit(event.kind, event.payload)
			}
		}
		if readErr != nil {
			break
		}
	}
	waitErr := cmd.Wait()
	close(finished)
	killMu.Lock()
	wasKilled := killed
	killMu.Unlock()

	result := runnerSessionTurnResult{Status: "ok", SessionCreated: capture.SessionID != "" || turn.Resume}
	if capture.Results > 0 {
		usage := map[string]any{}
		if capture.InputTokens != nil {
			usage["input_tokens"] = *capture.InputTokens
		}
		if capture.OutputTokens != nil {
			usage["output_tokens"] = *capture.OutputTokens
		}
		if capture.Model != "" {
			usage["model"] = capture.Model
		}
		if len(usage) > 0 {
			result.Usage = usage
		}
	}
	if tail := strings.TrimSpace(stderr.String()); tail != "" && (waitErr != nil || wasKilled) {
		emit("stderr", map[string]any{"text": truncateUTF8(tail, claudeMaxErrorBytes*4)})
	}
	switch {
	case wasKilled:
		result.Status, result.ErrorCode, result.Error = "error", "killed", "turn stopped"
	case capture.SessionID != "" && capture.SessionID != strings.ToLower(turn.HarnessSessionID):
		result.Status, result.ErrorCode, result.Error = "error", "harness_failed", "Claude Code reported a different session than the one requested"
	default:
		if res, err := claudeRunnerResult(capture); err != nil || res["status"] != "success" || waitErr != nil {
			result.Status = "error"
			result.ErrorCode, result.Error = claudeSessionFailure(capture, waitErr)
		}
	}
	return result
}

// claudeSessionFailure names a failed turn from Claude's plain-text startup
// errors, like claudeHostExecFailure does for flows.
func claudeSessionFailure(capture claudeCapture, waitErr error) (string, string) {
	line := capture.ErrorLine
	lower := strings.ToLower(line)
	switch {
	case strings.Contains(lower, "not logged in") || strings.Contains(lower, "please run /login") || strings.Contains(lower, "invalid api key"):
		return rejectHarnessSignedOut, "claude_not_logged_in: Claude Code on this runner has no usable login; run `claude auth login` as the runner user"
	case strings.Contains(lower, "model") && (strings.Contains(lower, "not found") || strings.Contains(lower, "not available") || strings.Contains(lower, "invalid")):
		return "model_not_available", line
	case capture.Subtype != "" && capture.Subtype != "success":
		return "harness_failed", "Claude Code ended the turn with " + capture.Subtype
	case line != "":
		return "harness_failed", line
	case waitErr != nil:
		return "harness_failed", waitErr.Error()
	default:
		return "harness_failed", "claude exited without a valid structured result"
	}
}

type claudeSessionEvent struct {
	kind    string
	payload map[string]any
}

// claudeSessionEvents maps one stream-json line to session events: assistant
// text, tool calls, tool results, the turn's usage, and plain-text errors.
// The init event and echoes of the operator prompt are dropped.
func claudeSessionEvents(capture *claudeCapture, line string) []claudeSessionEvent {
	applyClaudeLine(capture, line)
	trimmed := strings.TrimSpace(line)
	if trimmed == "" {
		return nil
	}
	if trimmed[0] != '{' {
		return []claudeSessionEvent{{"stderr", map[string]any{"text": truncateUTF8(trimmed, claudeMaxErrorBytes*4)}}}
	}
	var event struct {
		Type    string `json:"type"`
		Message struct {
			Model   string           `json:"model"`
			Content []map[string]any `json:"content"`
		} `json:"message"`
		Usage map[string]any `json:"usage"`
	}
	if json.Unmarshal([]byte(trimmed), &event) != nil {
		return nil
	}
	var out []claudeSessionEvent
	switch event.Type {
	case "assistant":
		for _, block := range event.Message.Content {
			switch block["type"] {
			case "text":
				payload := map[string]any{"event": "assistant"}
				if text, ok := block["text"].(string); ok {
					payload["text"] = text
				}
				if hostExecModelRe.MatchString(event.Message.Model) {
					payload["model"] = event.Message.Model
				}
				out = append(out, claudeSessionEvent{"agent_message", payload})
			case "tool_use":
				out = append(out, claudeSessionEvent{"tool_call", map[string]any{"event": "tool_use", "data": block}})
			}
		}
	case "user":
		for _, block := range event.Message.Content {
			if block["type"] == "tool_result" {
				out = append(out, claudeSessionEvent{"tool_result", map[string]any{"event": "tool_result", "data": block}})
			}
		}
	case "result":
		if event.Usage != nil {
			out = append(out, claudeSessionEvent{"usage", map[string]any{"event": "result", "usage": event.Usage}})
		}
	}
	return out
}
