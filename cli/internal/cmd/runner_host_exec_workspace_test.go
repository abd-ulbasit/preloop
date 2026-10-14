package cmd

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func TestJobHostExecWorkspace(t *testing.T) {
	spec, err := jobHostExecWorkspace(map[string]any{})
	if spec != nil || err != nil {
		t.Fatalf("absent workspace: %v %v", spec, err)
	}
	spec, err = jobHostExecWorkspace(map[string]any{hostExecWorkspaceKey: map[string]any{"kind": "authorized_directory", "id": "dir_ee", "allow_dirty": true}})
	if err != nil || spec.ID != "dir_ee" || !spec.AllowDirty || spec.Kind != workspaceKindAuthorizedDirectory {
		t.Fatalf("spec = %#v err = %v", spec, err)
	}
	refused := map[string]any{
		"not an object":  "dir_ee",
		"path kind":      map[string]any{"kind": "path", "path": "/srv/x"},
		"tracker":        map[string]any{"kind": "tracker_checkout", "tracker_id": "t"},
		"bad id":         map[string]any{"kind": "authorized_directory", "id": "../x"},
		"subpath":        map[string]any{"kind": "authorized_directory", "id": "dir_ee", "path": "../.."},
		"string dirty":   map[string]any{"kind": "authorized_directory", "id": "dir_ee", "allow_dirty": "yes"},
		"credential key": map[string]any{"kind": "authorized_directory", "id": "dir_ee", "credential": "x"},
	}
	for name, raw := range refused {
		if _, err := jobHostExecWorkspace(map[string]any{hostExecWorkspaceKey: raw}); err == nil {
			t.Errorf("%s: expected refusal", name)
		}
	}
}

// claudeAuthorizedProject creates a project directory authorized for
// harnesses and returns its canonical path.
func claudeAuthorizedProject(t *testing.T, mode string, harnesses ...string) string {
	t.Helper()
	project, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	entry := authorizedDirectory{ID: "dir_proj", Path: project, Mode: mode, Label: "proj"}
	if len(harnesses) > 0 {
		entry.Harnesses = authorizedHarnesses(harnesses)
	}
	writeAuthorizedDirectoriesForTest(t, entry)
	return project
}

func workspaceJob(extra map[string]any) map[string]any {
	ws := map[string]any{"kind": "authorized_directory", "id": "dir_proj"}
	for key, value := range extra {
		ws[key] = value
	}
	return claudeJob(map[string]any{hostExecWorkspaceKey: ws})
}

func TestClaudeHostJobRunsInAuthorizedDirectory(t *testing.T) {
	cwdFile := filepath.Join(t.TempDir(), "cwd")
	setupClaudeHost(t, true, `pwd -P > '`+cwdFile+`'
cat >/dev/null
cat <<'EOF'
`+claudeSuccessStream+`
EOF`)
	useHostFlowTestURL(t)
	project := claudeAuthorizedProject(t, authorizedDirModeWrite, hostExecHarnessClaude)
	job := workspaceJob(nil)
	job["host_exec_mcp"] = map[string]any{"token": hostFlowTestToken}
	run, err := newHostExecJob(job)
	if err != nil {
		t.Fatal(err)
	}
	defer run.cleanup()
	if run.cmd.Dir != project || run.workspace != project || run.workspaceLabel != "proj" {
		t.Fatalf("dir = %q workspace = %q label = %q", run.cmd.Dir, run.workspace, run.workspaceLabel)
	}
	config := cursorArgValue(run.cmd.Args[1:], "--mcp-config")
	if config == "" || strings.HasPrefix(config, project) {
		t.Fatalf("per-job MCP config must stay out of the authorized directory: %q", config)
	}
	entries, _ := os.ReadDir(project)
	if len(entries) != 0 {
		t.Fatalf("runner wrote into the authorized directory: %v", entries)
	}
	buffer := &runnerLogBuffer{native: true, harness: hostExecHarnessClaude}
	run.cmd.Stdout, run.cmd.Stderr = buffer, buffer
	if err := run.cmd.Start(); err != nil {
		t.Fatal(err)
	}
	outcome := waitHostExecJob(run.cmd, claudeTestExecID, buffer, nil, run.timeout, "claude-seat")
	if outcome.status != "SUCCEEDED" {
		t.Fatalf("outcome = %+v", outcome)
	}
	cwd, _ := os.ReadFile(cwdFile)
	if strings.TrimSpace(string(cwd)) != project {
		t.Fatalf("claude ran in %q, want %q", cwd, project)
	}
}

func TestHostJobAuthorizedDirectoryRefusals(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null`)
	cases := map[string]struct {
		mode      string
		harnesses []string
		job       map[string]any
		want      string
	}{
		"harness not allowed": {authorizedDirModeWrite, []string{"copilot_cli"}, workspaceJob(nil), "does not allow harness claude_code"},
		"read only":           {authorizedDirModeReadOnly, nil, workspaceJob(nil), "read_only"},
		"unknown id":          {authorizedDirModeWrite, nil, claudeJob(map[string]any{hostExecWorkspaceKey: map[string]any{"kind": "authorized_directory", "id": "dir_other"}}), "no authorized directory"},
		"checkout": {authorizedDirModeWrite, nil, func() map[string]any {
			job := workspaceJob(nil)
			job["host_exec_checkout"] = map[string]any{"repositories": []any{map[string]any{"url": "https://example.com/a/b.git", "path": "b"}}}
			return job
		}(), "host_exec_workspace_conflict"},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			claudeAuthorizedProject(t, tc.mode, tc.harnesses...)
			_, err := newHostExecJob(tc.job)
			if err == nil || !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("err = %v, want %q", err, tc.want)
			}
		})
	}
}

func TestHostJobAuthorizedDirectoryRefusesHome(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null`)
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatal(err)
	}
	canonical, err := filepath.EvalSymlinks(home)
	if err != nil {
		t.Fatal(err)
	}
	// Written directly: `runner dirs add` already refuses the home directory,
	// so this also proves the job re-checks the entry.
	if err := writeAuthorizedDirectories([]authorizedDirectory{{ID: "dir_proj", Path: canonical, Mode: authorizedDirModeWrite}}); err != nil {
		t.Fatal(err)
	}
	if _, err := newHostExecJob(workspaceJob(nil)); err == nil || !strings.Contains(err.Error(), workspaceErrNotAuthorized) {
		t.Fatalf("home directory accepted: %v", err)
	}
}

func TestHostJobAuthorizedDirectoryDirtyTree(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not installed")
	}
	setupClaudeHost(t, true, `cat >/dev/null`)
	project := claudeAuthorizedProject(t, authorizedDirModeWrite)
	if out, err := exec.Command("git", "-C", project, "init", "-q").CombinedOutput(); err != nil {
		t.Fatalf("git init: %v %s", err, out)
	}
	if err := os.WriteFile(filepath.Join(project, "notes.md"), []byte("uncommitted\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Lease time stays fast: the dirty check is deferred to the job goroutine.
	run, err := newHostExecJob(workspaceJob(nil))
	if err != nil {
		t.Fatalf("newHostExecJob must not run the dirty check: %v", err)
	}
	run.cleanup()
	if run.dirtyCheckDir != project {
		t.Fatalf("dirty check not scheduled: %q", run.dirtyCheckDir)
	}
	if err := hostExecWorkspaceDirtyCheck(context.Background(), run.dirtyCheckDir, run.workspaceLabel); err == nil || !strings.HasPrefix(err.Error(), workspaceErrDirty) {
		t.Fatalf("dirty tree accepted without allow_dirty: %v", err)
	}
	if out, err := exec.Command("git", "-C", project, "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A").CombinedOutput(); err != nil {
		t.Fatalf("git add: %v %s", err, out)
	}
	if out, err := exec.Command("git", "-C", project, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "x").CombinedOutput(); err != nil {
		t.Fatalf("git commit: %v %s", err, out)
	}
	if err := hostExecWorkspaceDirtyCheck(context.Background(), project, "proj"); err != nil {
		t.Fatalf("clean tree refused: %v", err)
	}
}

func TestHostJobAuthorizedDirectoryAllowDirtySkipsCheck(t *testing.T) {
	setupClaudeHost(t, true, `cat >/dev/null`)
	project := claudeAuthorizedProject(t, authorizedDirModeWrite)
	run, err := newHostExecJob(workspaceJob(map[string]any{"allow_dirty": true}))
	if err != nil {
		t.Fatalf("allow_dirty refused: %v", err)
	}
	run.cleanup()
	if run.cmd.Dir != project || run.dirtyCheckDir != "" {
		t.Fatalf("dir = %q dirty check = %q", run.cmd.Dir, run.dirtyCheckDir)
	}
}

func TestBeginHostExecJobFailsDirtyTreeBeforeStart(t *testing.T) {
	if _, err := exec.LookPath("git"); err != nil {
		t.Skip("git not installed")
	}
	marker := filepath.Join(t.TempDir(), "started")
	setupClaudeHost(t, true, `touch '`+marker+`'`)
	project := claudeAuthorizedProject(t, authorizedDirModeWrite)
	if out, err := exec.Command("git", "-C", project, "init", "-q").CombinedOutput(); err != nil {
		t.Fatalf("git init: %v %s", err, out)
	}
	if err := os.WriteFile(filepath.Join(project, "notes.md"), []byte("x\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	jobs := newRunnerJobs(1)
	var halted atomic.Bool
	if err := beginHostExecJob(nil, workspaceJob(nil), claudeTestExecID, &halted, jobs); err != nil {
		t.Fatal(err)
	}
	select {
	case outcome := <-jobs.outcomes:
		if outcome.status != "FAILED" || !strings.HasPrefix(outcome.errMsg, workspaceErrDirty) {
			t.Fatalf("outcome = %+v", outcome)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("no outcome")
	}
	if _, err := os.Stat(marker); err == nil {
		t.Fatal("claude started in a dirty directory")
	}
}

func TestValidateHostExecWorkspaceJobMCPLocation(t *testing.T) {
	spec := &sessionWorkspaceSpec{Kind: workspaceKindAuthorizedDirectory, ID: "dir_proj"}
	for _, harness := range []string{hostExecHarnessCursor, hostExecHarnessDevin} {
		if err := validateHostExecWorkspaceJob(spec, harness, nil, "token"); err == nil {
			t.Errorf("%s: an MCP config in the operator's directory must be refused", harness)
		}
		if err := validateHostExecWorkspaceJob(spec, harness, nil, ""); err != nil {
			t.Errorf("%s without MCP: %v", harness, err)
		}
	}
	for _, harness := range []string{hostExecHarnessClaude, hostExecHarnessCopilot} {
		if err := validateHostExecWorkspaceJob(spec, harness, nil, "token"); err != nil {
			t.Errorf("%s: %v", harness, err)
		}
	}
}
