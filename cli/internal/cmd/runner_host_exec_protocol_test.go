package cmd

import (
	"strings"
	"testing"
)

func TestNativeOpenCodeCompletionProtocol(t *testing.T) {
	var c openCodeCapture
	applyOpenCodeLine(&c, `{"type":"step_finish","sessionID":"ses_test","part":{"reason":"tool-calls"}}`)
	if _, err := openCodeRunnerResult(c); err == nil {
		t.Fatal("tool step is not completion")
	}
	applyOpenCodeLine(&c, `{"type":"step_finish","sessionID":"ses_test","part":{"reason":"stop","tokens":{"input":12,"output":3}}}`)
	result, err := openCodeRunnerResult(c)
	if err != nil || result["session_id"] != "ses_test" {
		t.Fatalf("completion: %v %v", result, err)
	}
	applyOpenCodeLine(&c, `{"type":"error","sessionID":"ses_test"}`)
	if _, err := openCodeRunnerResult(c); err == nil {
		t.Fatal("error must remain sticky")
	}
}
func TestNativeDeepSeekCompletionProtocol(t *testing.T) {
	var c deepSeekCapture
	applyDeepSeekLine(&c, `{"type":"session","sessionId":"session-test"}`)
	applyDeepSeekLine(&c, `{"type":"final","text":""}`)
	if _, err := deepSeekRunnerResult(c); err == nil {
		t.Fatal("final alone is not success")
	}
	applyDeepSeekLine(&c, `{"type":"status","phase":"turn_end","reason":"completed"}`)
	if _, err := deepSeekRunnerResult(c); err != nil {
		t.Fatal(err)
	}
	applyDeepSeekLine(&c, `{"type":"final","text":"duplicate"}`)
	if _, err := deepSeekRunnerResult(c); err == nil {
		t.Fatal("duplicate final")
	}
	var failure deepSeekCapture
	applyDeepSeekLine(&failure, `{"type":"session","sessionId":"session-test"}`)
	applyDeepSeekLine(&failure, `{"type":"status","phase":"turn_end","reason":"error"}`)
	applyDeepSeekLine(&failure, `{"type":"final","text":""}`)
	if _, err := deepSeekRunnerResult(failure); err == nil {
		t.Fatal("in-turn failure can emit final")
	}
}
func TestNativeProtocolProfilesRejectUnsafeFlags(t *testing.T) {
	for _, validate := range []func(hostExecProfile) error{validateOpenCodeHostExecProfile, validateDeepSeekHostExecProfile} {
		for _, p := range []hostExecProfile{{Argv: []string{"--auto"}}, {AllowAllTools: true}, {ForceWrites: true}, {DenyTools: []string{"shell"}}} {
			if validate(p) == nil {
				t.Fatal("unsafe profile accepted")
			}
		}
	}
}

func TestNativeDeepSeekInstallerPinsJSONProtocol(t *testing.T) {
	for _, kind := range []string{"deepseek", "dsh", "DeepSeek Harness"} {
		spec, err := runtimeInstallSpecForKind(kind)
		if err != nil {
			t.Fatal(err)
		}
		if !strings.Contains(strings.Join(spec.installCommand, " "), "@deepseek-ai/dsh@0.2.0-rc.2") {
			t.Fatal("installer must supply verified JSON/stdin protocol")
		}
	}
}
