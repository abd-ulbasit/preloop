package cmd

import (
	"bufio"
	"encoding/json"
	"fmt"
	"gopkg.in/yaml.v3"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// The test executable is a portable native fake, including on Windows.
func TestNativeProtocolChildProcess(t *testing.T) {
	mode := os.Getenv("PRELOOP_PROTOCOL_TEST_CHILD")
	if mode == "" {
		return
	}
	if mode == "installer" {
		if os.Getenv("PRELOOP_TOKEN") != "" {
			os.Exit(43)
		}
		fmt.Print("installed")
		os.Exit(0)
	}
	if mode == "gate-ready" || mode == "gate-missing" {
		input := make(chan string, 1)
		go func() { data, _ := io.ReadAll(os.Stdin); input <- string(data) }()
		select {
		case <-input:
			os.Exit(44)
		case <-time.After(75 * time.Millisecond):
		}
		if mode == "gate-ready" {
			fmt.Println(`{"type":"preloop_governance_ready","nonce":"native-gate"}`)
		}
		select {
		case task := <-input:
			if mode == "gate-missing" {
				_ = os.WriteFile(os.Getenv("PRELOOP_PROTOCOL_INPUT_PROBE"), []byte(task), 0600)
				os.Exit(45)
			}
			if task != "portable task" {
				os.Exit(46)
			}
			fmt.Println(`{"type":"session","sessionId":"session-gated"}`)
			fmt.Println(`{"type":"status","phase":"turn_end","reason":"completed"}`)
			fmt.Println(`{"type":"final","text":"done"}`)
			os.Exit(0)
		case <-time.After(time.Minute):
			os.Exit(47)
		}
	}
	if mode == "wait" {
		fmt.Println("ready")
		time.Sleep(time.Minute)
		os.Exit(0)
	}
	prompt, _ := io.ReadAll(os.Stdin)
	if string(prompt) != "portable task" {
		os.Exit(41)
	}
	if mode == "codex" {
		fmt.Println(`{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}`)
		fmt.Println(`{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}`)
		os.Exit(0)
	}
	path := os.Getenv("OPENCODE_CONFIG")
	if mode == "deepseek" {
		for i, arg := range os.Args {
			if arg == "--patch" && i+1 < len(os.Args) {
				path = os.Args[i+1]
			}
		}
	}
	data, err := os.ReadFile(path)
	if err != nil || !strings.Contains(string(data), "preloop") {
		os.Exit(42)
	}
	if mode == "opencode" {
		fmt.Println(`{"type":"step_finish","sessionID":"ses_portable","part":{"reason":"stop"}}`)
	} else {
		fmt.Println(`{"type":"session","sessionId":"session-portable"}`)
		fmt.Println(`{"type":"status","phase":"turn_end","reason":"completed"}`)
		fmt.Println(`{"type":"final","text":"done"}`)
	}
	os.Exit(0)
}
func TestNativePreparedProtocolProcesses(t *testing.T) {
	previousURL := FlagURL
	FlagURL = "https://example.invalid"
	t.Cleanup(func() { FlagURL = previousURL })
	const testMCPToken = "synthetic-protocol-token"

	home := filepath.Join(t.TempDir(), "home # % with space")
	if err := os.MkdirAll(home, 0700); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HOME", home)
	t.Setenv("USERPROFILE", home)
	t.Setenv("DSH_HOME", filepath.Join(home, ".dsh"))
	t.Setenv("CODEX_HOME", filepath.Join(home, ".codex"))
	if err := writeJSONDocument(filepath.Join(home, ".codex", "hooks.json"), map[string]any{"hooks": map[string]any{"PreToolUse": []any{map[string]any{"hooks": []any{map[string]any{"type": "command", "command": "preloop agents permission-hook --source codex_cli"}}}}}}); err != nil {
		t.Fatal(err)
	}
	cache := t.TempDir()
	t.Setenv("XDG_CACHE_HOME", cache)
	openCodeMock := filepath.Join(cache, "opencode", "packages", "@preloop-ai", "opencode-plugin@latest", "node_modules", "@preloop-ai", "opencode-plugin")
	if err := os.MkdirAll(openCodeMock, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(openCodeMock, "package.json"), []byte(`{"name":"@preloop-ai/opencode-plugin","main":"index.mjs","type":"module"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(openCodeMock, "index.mjs"), []byte(`export default async function() { return {'tool.execute.before': async () => {}}; }`), 0600); err != nil {
		t.Fatal(err)
	}
	control := map[string]any{"native_tool_approvals": "on", "control_ws_url": "wss://example.invalid/control/ws", "bearer_token": "test-only"}
	if err := writeJSONDocument(openCodeUserConfigPath(), map[string]any{"plugin": []any{openCodePluginPackageName}, "preloop": map[string]any{"control": control}}); err != nil {
		t.Fatal(err)
	}
	if err := writeJSONDocument(deepSeekControlConfigPath(), map[string]any{"preloop": map[string]any{"control": control}}); err != nil {
		t.Fatal(err)
	}
	entry := harnessPluginEntry(AgentConfig{Name: "DeepSeek Harness"})
	if err := os.MkdirAll(filepath.Dir(entry), 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(entry, []byte("export function apply() {}"), 0600); err != nil {
		t.Fatal(err)
	}
	for _, tc := range []struct {
		name    string
		prepare func(hostExecProfile, map[string]any, string, string, string) ([]string, string, []string, []string, error)
	}{{"opencode", prepareOpenCodeHostExec}, {"deepseek", prepareDeepSeekHostExec}, {"codex", prepareCodexHostExec}} {
		t.Run(tc.name, func(t *testing.T) {
			args, prompt, env, files, err := tc.prepare(hostExecProfile{}, map[string]any{"prompt": "portable task"}, t.TempDir(), t.TempDir(), testMCPToken)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(strings.Join(args, " "), testMCPToken) {
				t.Fatal("token exposed in argv")
			}
			for _, path := range files {
				data, readErr := os.ReadFile(path)
				if readErr != nil {
					t.Fatal(readErr)
				}
				if tc.name == "deepseek" && strings.HasSuffix(path, ".yml") {
					var rows []map[string]any
					if err := yaml.Unmarshal(data, &rows); err != nil {
						t.Fatal(err)
					}
					for _, id := range []string{"credentials", "authorization", "deepseek-account"} {
						found := false
						for _, row := range rows {
							if row["id"] == id && row["disabled"] == false {
								found = true
							}
						}
						if !found {
							t.Fatalf("native authentication service %s not restored", id)
						}
					}
				}
				if !strings.HasSuffix(path, ".mjs") && !strings.Contains(string(data), testMCPToken) {
					t.Fatal("missing MCP credential in private config")
				}
			}
			if tc.name != "codex" {
				if node, nodeErr := exec.LookPath("node"); nodeErr == nil {
					var wrapper string
					for _, path := range files {
						if strings.HasSuffix(path, ".mjs") {
							wrapper = path
						}
					}
					script := `import {pathToFileURL} from 'node:url';const m=await import(pathToFileURL(process.argv[1]).href);if(m.default)await m.default({client:{mcp:{status:async()=>({data:{"preloop-flow":{status:"connected"}}})}}});else await m.apply({get:()=>({await:async()=>{}}),tools:{schemas:()=>[{name:'mcp__preloop-flow__test'}]}},{});`
					invoke := exec.Command(node, "--input-type=module", "-e", script, wrapper)
					invoke.Env = append(os.Environ(), env...)
					output, nodeErr := invoke.CombinedOutput()
					if nodeErr != nil {
						t.Fatalf("wrapper: %v %s", nodeErr, output)
					}
					if !strings.HasSuffix(string(output), "\n") || !strings.Contains(string(output), `"type":"preloop_governance_ready"`) {
						t.Fatalf("missing newline-delimited readiness: %q", output)
					}
					missingScript := strings.ReplaceAll(script, "connected", "failed")
					missingScript = strings.ReplaceAll(missingScript, "mcp__preloop-flow__test", "mcp__wrong__test")
					invoke = exec.Command(node, "--input-type=module", "-e", missingScript, wrapper)
					invoke.Env = append(os.Environ(), env...)
					missingOutput, missingErr := invoke.CombinedOutput()
					if missingErr != nil || strings.Contains(string(missingOutput), `"type":"preloop_governance_ready"`) {
						t.Fatalf("missing MCP accepted: %v %s", missingErr, missingOutput)
					}
					var acknowledged map[string]any
					if err := json.Unmarshal([]byte(strings.TrimSpace(string(output))), &acknowledged); err != nil {
						t.Fatalf("ack is not one JSON line: %v %q", err, output)
					}
					mockPath := entry
					if tc.name == "opencode" {
						mockPath = filepath.Join(openCodeMock, "index.mjs")
					}
					if err := os.WriteFile(mockPath, []byte(`export function apply(){throw new Error('fail closed')} export default apply;`), 0600); err != nil {
						t.Fatal(err)
					}
					invoke = exec.Command(node, "--input-type=module", "-e", script, wrapper)
					invoke.Env = append(os.Environ(), env...)
					output, nodeErr = invoke.CombinedOutput()
					if nodeErr == nil || strings.Contains(string(output), `"type":"preloop_governance_ready"`) {
						t.Fatalf("failed plugin acknowledged: %v %s", nodeErr, output)
					}
				}
			}
			childArgs := append([]string{"-test.run=^TestNativeProtocolChildProcess$", "--"}, args...)
			child := exec.Command(os.Args[0], childArgs...)
			child.Env = append(os.Environ(), env...)
			child.Env = append(child.Env, "PRELOOP_PROTOCOL_TEST_CHILD="+tc.name)
			child.Stdin = strings.NewReader(prompt)
			output, err := child.CombinedOutput()
			if err != nil {
				t.Fatalf("child: %v %s", err, output)
			}
			var cx codexCapture
			var oc openCodeCapture
			var ds deepSeekCapture
			for _, line := range strings.Split(string(output), "\n") {
				if tc.name == "codex" {
					applyCodexLine(&cx, line)
				} else if tc.name == "opencode" {
					applyOpenCodeLine(&oc, line)
				} else {
					applyDeepSeekLine(&ds, line)
				}
			}
			if tc.name == "codex" {
				_, err = codexRunnerResult(cx)
			} else if tc.name == "opencode" {
				_, err = openCodeRunnerResult(oc)
			} else {
				_, err = deepSeekRunnerResult(ds)
			}
			if err != nil {
				t.Fatal(err)
			}
			for _, path := range files {
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
				if _, err := os.Stat(path); !os.IsNotExist(err) {
					t.Fatal("config survived cleanup")
				}
			}
		})
	}
}
func TestNativeProtocolProcessCancellation(t *testing.T) {
	child := exec.Command(os.Args[0], "-test.run=^TestNativeProtocolChildProcess$")
	child.Env = append(os.Environ(), "PRELOOP_PROTOCOL_TEST_CHILD=wait")
	child.SysProcAttr = hostExecSysProcAttr()
	stdout, err := child.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err = child.Start(); err != nil {
		t.Fatal(err)
	}
	defer killRunnerJobProcess(child)
	ready := make(chan bool, 1)
	go func() { s := bufio.NewScanner(stdout); ready <- s.Scan() && s.Text() == "ready" }()
	select {
	case ok := <-ready:
		if !ok {
			t.Fatal("child did not start")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("startup timeout")
	}
	killRunnerJobProcess(child)
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("cancelled child succeeded")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("cancel did not terminate child")
	}
}

func TestNativeRuntimeInstallerProcess(t *testing.T) {
	t.Setenv("PRELOOP_PROTOCOL_TEST_CHILD", "installer")
	t.Setenv("PRELOOP_TOKEN", "test-only-bootstrap")
	var output strings.Builder
	if err := runRuntimeInstallCommand([]string{os.Args[0], "-test.run=^TestNativeProtocolChildProcess$"}, &output); err != nil {
		t.Fatal(err)
	}
	if output.String() != "installed" {
		t.Fatalf("installer output %q", output.String())
	}
}

func TestNativeProtocolGovernanceGateProcesses(t *testing.T) {
	for _, mode := range []string{"gate-ready", "gate-missing"} {
		t.Run(mode, func(t *testing.T) {
			child := exec.Command(os.Args[0], "-test.run=^TestNativeProtocolChildProcess$")
			probe := filepath.Join(t.TempDir(), "input-arrived")
			child.Env = append(os.Environ(), "PRELOOP_PROTOCOL_TEST_CHILD="+mode, "PRELOOP_PROTOCOL_INPUT_PROBE="+probe)
			child.SysProcAttr = hostExecSysProcAttr()
			gate, err := newHostExecGovernanceGate(child, "portable task", "native-gate")
			if err != nil {
				t.Fatal(err)
			}
			gate.timeout = 2 * time.Second
			if mode == "gate-missing" {
				gate.timeout = 250 * time.Millisecond
			}
			buffer := &runnerLogBuffer{native: true, harness: hostExecHarnessDeepSeek, governance: gate}
			child.Stdout = buffer
			child.Stderr = buffer
			if err := child.Start(); err != nil {
				t.Fatal(err)
			}
			defer killRunnerJobProcess(child)
			defer gate.close()
			go gate.deliver(child)
			done := make(chan error, 1)
			go func() { done <- child.Wait() }()
			select {
			case err = <-done:
			case <-time.After(10 * time.Second):
				t.Fatal("child never settled")
			}
			<-gate.delivered
			if mode == "gate-ready" {
				if err != nil || gate.failed.Load() {
					t.Fatalf("gated child: %v", err)
				}
				if _, err = deepSeekRunnerResult(buffer.deepSeekCapture); err != nil {
					t.Fatal(err)
				}
			} else {
				if err == nil || !gate.failed.Load() {
					t.Fatal("missing governance accepted")
				}
				if _, probeErr := os.Stat(probe); !os.IsNotExist(probeErr) {
					t.Fatal("ungoverned child received task or EOF")
				}
			}
		})
	}
}
