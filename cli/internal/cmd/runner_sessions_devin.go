package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

type devinSessionAdapter struct{}

func (a *devinSessionAdapter) Harness() string                 { return hostExecHarnessDevin }
func (a *devinSessionAdapter) Mode() string                    { return "resume" }
func (a *devinSessionAdapter) Stop(*runnerRemoteSession) error { return nil }
func devinSessionProfile() (hostExecProfile, error) {
	profiles, err := loadHostExecProfiles()
	if err != nil {
		return hostExecProfile{}, err
	}
	for _, profile := range profiles {
		if strings.EqualFold(profile.Name, "devin") {
			if hostExecProfileHarness(profile) != hostExecHarnessDevin {
				return hostExecProfile{}, fmt.Errorf("devin profile must run Devin CLI")
			}
			return profile, nil
		}
	}
	return hostExecProfile{Name: "devin", Executable: "devin"}, nil
}
func (a *devinSessionAdapter) Start(s *runnerRemoteSession) error {
	if !devinApprovalHooksInstalled() {
		return fmt.Errorf("devin_approval_hook_missing: run preloop agents onboard Devin --approvals as the runner user")
	}
	profile, err := devinSessionProfile()
	if err != nil {
		return err
	}
	_, _, err = resolveHostExecCommand(profile.Executable)
	return err
}
func (a *devinSessionAdapter) Turn(ctx context.Context, turn runnerSessionTurnSpec, emit func(string, map[string]any)) runnerSessionTurnResult {
	fail := func(code string, err error) runnerSessionTurnResult {
		return runnerSessionTurnResult{Status: "error", ErrorCode: code, Error: err.Error()}
	}
	if turn.Dir == "" {
		return fail(rejectWorkspaceNotAllowed, fmt.Errorf("no workspace"))
	}
	if !devinApprovalHooksInstalled() {
		return fail("devin_approval_hook_missing", fmt.Errorf("Preloop hooks are no longer installed"))
	}
	profile, err := devinSessionProfile()
	if err != nil {
		return fail("harness_unavailable", err)
	}
	job := map[string]any{"prompt": turn.Text}
	if model := strings.TrimSpace(turn.Model); model != "" && model != "auto" {
		if !hostExecModelRe.MatchString(model) {
			return fail("invalid_turn", fmt.Errorf("invalid model id"))
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
	path, err := newDevinExportPath()
	if err != nil {
		return fail("harness_failed", err)
	}
	defer os.Remove(path)
	resume := ""
	if turn.Resume {
		resume = turn.HarnessSessionID
		if resume == "" {
			return fail("invalid_turn", fmt.Errorf("missing Devin session id"))
		}
	}
	args, err := buildDevinHostExecArgs(profile, job, path, resume)
	if err != nil {
		return fail("invalid_turn", err)
	}
	bin, prefix, err := resolveHostExecCommand(profile.Executable)
	if err != nil {
		return fail("harness_unavailable", err)
	}
	args = append(prefix, args...)
	if err := hostExecCommandLineError(runtime.GOOS, bin, args); err != nil {
		return fail("invalid_turn", err)
	}
	cmd := exec.Command(bin, args...)
	cmd.Dir = turn.Dir
	cmd.Env = hostExecPrependPath(runtime.GOOS, hostExecChildEnv(runtime.GOOS, hostExecHarnessDevin, profile, os.Environ()), hostExecBinaryDirs(bin, profile.Executable)...)
	cmd.SysProcAttr = hostExecSysProcAttr()
	cmd.WaitDelay = 250 * time.Millisecond
	output := &boundedTail{max: 64 * 1024}
	cmd.Stdout = output
	cmd.Stderr = output
	if err := cmd.Start(); err != nil {
		return fail("harness_unavailable", err)
	}
	finished := make(chan struct{})
	defer close(finished)
	defer killRunnerJobProcess(cmd)
	go func() {
		select {
		case <-ctx.Done():
			killRunnerJobProcess(cmd)
		case <-finished:
		}
	}()
	err = cmd.Wait()
	if ctx.Err() != nil {
		return fail("killed", ctx.Err())
	}
	if err != nil {
		emit("stderr", map[string]any{"text": output.String()})
		return fail("harness_failed", err)
	}
	export, err := readDevinExport(path)
	if err != nil {
		return fail("harness_failed", err)
	}
	if resume != "" && export.SessionID != resume {
		return fail("harness_failed", fmt.Errorf("Devin resumed a different session"))
	}
	last := export.Steps[len(export.Steps)-1]
	var text string
	if json.Unmarshal(last.Message, &text) != nil {
		text = string(last.Message)
	}
	emit("agent_message", map[string]any{"text": truncateUTF8(text, 64*1024)})
	return runnerSessionTurnResult{Status: "ok", SessionCreated: true, HarnessSessionID: export.SessionID}
}
