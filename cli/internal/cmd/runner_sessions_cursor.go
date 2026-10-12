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
	"time"
)

// cursorSessionAdapter resumes a chat created by the host's Cursor CLI.
// Host consent and installed approval hooks are required for every turn.
type cursorSessionAdapter struct{}

func (a *cursorSessionAdapter) Harness() string                 { return hostExecHarnessCursor }
func (a *cursorSessionAdapter) Mode() string                    { return "resume" }
func (a *cursorSessionAdapter) Stop(*runnerRemoteSession) error { return nil }

func cursorSessionProfile() (hostExecProfile, error) {
	profiles, err := loadHostExecProfiles()
	if err != nil {
		return hostExecProfile{}, err
	}
	for _, profile := range profiles {
		if strings.EqualFold(profile.Name, "cursor") {
			if !hostExecIsCursorBinary(profile.Executable) {
				return hostExecProfile{}, fmt.Errorf("cursor profile must run Cursor CLI")
			}
			return profile, nil
		}
	}
	return hostExecProfile{Name: "cursor", Executable: "cursor-agent"}, nil
}

func cursorSessionHooksInstalled() bool {
	return cursorPreloopBeforeHookInstalled("preToolUse") &&
		cursorPreloopBeforeHookInstalled("beforeShellExecution") &&
		cursorPreloopBeforeHookInstalled("beforeMCPExecution")
}

func (a *cursorSessionAdapter) Start(s *runnerRemoteSession) error {
	if !cursorSessionHooksInstalled() {
		return fmt.Errorf("cursor_approval_hook_missing: run preloop agents onboard Cursor --approvals as the runner user")
	}
	profile, err := cursorSessionProfile()
	if err != nil {
		return err
	}
	bin, prefix, err := resolveHostExecCommand(profile.Executable)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, bin, append(prefix, "create-chat")...)
	cmd.Env = hostExecPrependPath(runtime.GOOS, hostExecChildEnv(runtime.GOOS, hostExecHarnessCursor, profile, os.Environ()), hostExecBinaryDirs(bin, profile.Executable)...)
	cmd.Dir = s.WorkspaceDir
	out, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("cursor_create_chat_failed: %w", err)
	}
	id := strings.TrimSpace(string(out))
	if !copilotSessionIDRe.MatchString(id) {
		return fmt.Errorf("cursor_create_chat_failed: invalid chat id")
	}
	s.HarnessSessionID = id
	return nil
}

func cursorSessionArgs(profile hostExecProfile, turn runnerSessionTurnSpec) ([]string, error) {
	if !copilotSessionIDRe.MatchString(turn.HarnessSessionID) {
		return nil, fmt.Errorf("invalid harness session id")
	}
	job := map[string]any{"prompt": turn.Text}
	if model := strings.TrimSpace(turn.Model); model != "" && model != "auto" {
		if !hostExecModelRe.MatchString(model) {
			return nil, fmt.Errorf("invalid model id")
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
	return buildHostExecArgs(profile, job, turn.Dir, "--resume", turn.HarnessSessionID)
}

func (a *cursorSessionAdapter) Turn(ctx context.Context, turn runnerSessionTurnSpec, emit func(string, map[string]any)) runnerSessionTurnResult {
	fail := func(code string, err error) runnerSessionTurnResult {
		return runnerSessionTurnResult{Status: "error", ErrorCode: code, Error: err.Error()}
	}
	if turn.Dir == "" {
		return fail(rejectWorkspaceNotAllowed, fmt.Errorf("no workspace"))
	}
	if !cursorSessionHooksInstalled() {
		return fail("cursor_approval_hook_missing", fmt.Errorf("Preloop approval hooks are no longer installed"))
	}
	profile, err := cursorSessionProfile()
	if err != nil {
		return fail("harness_unavailable", err)
	}
	args, err := cursorSessionArgs(profile, turn)
	if err != nil {
		return fail("invalid_turn", err)
	}
	bin, prefix, err := resolveHostExecCommand(profile.Executable)
	if err != nil {
		return fail("harness_unavailable", err)
	}
	args = append(append([]string{}, prefix...), args...)
	if err := hostExecCommandLineError(runtime.GOOS, bin, args); err != nil {
		return fail("invalid_turn", err)
	}
	cmd := exec.Command(bin, args...)
	cmd.Dir = turn.Dir
	cmd.Env = hostExecPrependPath(runtime.GOOS, hostExecChildEnv(runtime.GOOS, hostExecHarnessCursor, profile, os.Environ()), hostExecBinaryDirs(bin, profile.Executable)...)
	cmd.SysProcAttr = hostExecSysProcAttr()
	cmd.WaitDelay = 250 * time.Millisecond
	defer killRunnerJobProcess(cmd)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return fail("harness_unavailable", err)
	}
	stderr := &boundedTail{max: 2048}
	cmd.Stderr = stderr
	if err := cmd.Start(); err != nil {
		return fail("harness_unavailable", err)
	}
	finished := make(chan struct{})
	defer close(finished)
	go func() {
		select {
		case <-ctx.Done():
			killRunnerJobProcess(cmd)
		case <-finished:
		}
	}()
	capture := cursorCapture{}
	reader := bufio.NewReaderSize(stdout, 64*1024)
	for {
		line, readErr := readBoundedLine(reader, 4*1024*1024)
		var event cursorStreamEvent
		if json.Unmarshal([]byte(line), &event) == nil {
			applyCursorEvent(&capture, event)
			var payload map[string]any
			if json.Unmarshal([]byte(line), &payload) == nil {
				switch event.Type {
				case "assistant":
					emit("agent_message", payload)
				case "tool_call":
					emit("tool_call", payload)
				case "result":
					emit("usage", payload)
				}
			}
		}
		if readErr != nil {
			break
		}
	}
	waitErr := cmd.Wait()
	if ctx.Err() != nil {
		return fail("killed", ctx.Err())
	}
	if tail := strings.TrimSpace(stderr.String()); tail != "" && waitErr != nil {
		emit("stderr", map[string]any{"text": tail})
	}
	if waitErr != nil {
		return fail("harness_failed", waitErr)
	}
	if !capture.HasResult || capture.ResultErr {
		return fail("harness_failed", fmt.Errorf("Cursor exited without a successful structured result"))
	}
	return runnerSessionTurnResult{Status: "ok", SessionCreated: true}
}
