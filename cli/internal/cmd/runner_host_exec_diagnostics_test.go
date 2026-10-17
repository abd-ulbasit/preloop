package cmd

import (
	"strings"
	"testing"
)

func TestNativeDiagnosticFramesDoNotCorruptProtocol(t *testing.T) {
	cases := []struct {
		harness string
		lines   []string
		result  func(*runnerLogBuffer) error
	}{
		{hostExecHarnessCodex, []string{`{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}`, `{"type":"turn.completed"}`}, func(b *runnerLogBuffer) error { _, err := codexRunnerResult(b.codexCapture); return err }},
		{hostExecHarnessOpenCode, []string{`{"type":"step_finish","sessionID":"ses_diagnostic","part":{"reason":"stop"}}`}, func(b *runnerLogBuffer) error { _, err := openCodeRunnerResult(b.openCodeCapture); return err }},
		{hostExecHarnessDeepSeek, []string{`{"type":"session","sessionId":"session-diagnostic"}`, `{"type":"status","phase":"turn_end","reason":"completed"}`, `{"type":"final","text":"done"}`}, func(b *runnerLogBuffer) error { _, err := deepSeekRunnerResult(b.deepSeekCapture); return err }},
	}
	for _, tc := range cases {
		t.Run(tc.harness, func(t *testing.T) {
			const secret = "synthetic-scoped-token"
			b := &runnerLogBuffer{native: true, harness: tc.harness, secrets: []string{secret}}
			diagnostic := runnerDiagnosticWriter{buffer: b}
			for _, line := range tc.lines {
				_, _ = diagnostic.Write([]byte(line + "\n"))
			}
			if tc.result(b) == nil {
				t.Fatal("stderr forged native completion")
			}
			for _, line := range tc.lines {
				split := len(line) / 2
				_, _ = b.Write([]byte(line[:split]))
				_, _ = diagnostic.Write([]byte("diagnostic synthetic-scoped-"))
				_, _ = b.Write([]byte(line[split:] + "\n"))
				_, _ = diagnostic.Write([]byte("token\n"))
			}
			_, _ = diagnostic.Write([]byte("unterminated " + secret))
			b.finish()
			if err := tc.result(b); err != nil {
				t.Fatalf("stderr corrupted stdout completion: %v", err)
			}
			logs := strings.Join(b.pending, "\n")
			if strings.Contains(logs, secret) || !strings.Contains(logs, "diagnostic [REDACTED]") || !strings.Contains(logs, "unterminated [REDACTED]") {
				t.Fatalf("diagnostic redaction/framing: %s", logs)
			}
		})
	}
}
func TestNativeDiagnosticGovernanceAndBounds(t *testing.T) {
	gate := &hostExecGovernanceGate{nonce: "diagnostic-nonce", ready: make(chan struct{})}
	b := &runnerLogBuffer{native: true, harness: hostExecHarnessOpenCode, governance: gate}
	diagnostic := runnerDiagnosticWriter{buffer: b}
	_, _ = b.Write([]byte(`{"type":"text","sessionID":"ses_test","part":`))
	_, _ = diagnostic.Write([]byte(`{"type":"preloop_governance_ready","nonce":"diagnostic-`))
	_, _ = b.Write([]byte(`{"text":"ok"}}` + "\n"))
	_, _ = diagnostic.Write([]byte("nonce\"}\n"))
	if !gate.readySeen.Load() {
		t.Fatal("stderr governance ack was lost")
	}
	if strings.Contains(strings.Join(b.pending, "\n"), "diagnostic-nonce") {
		t.Fatal("internal acknowledgement entered user logs")
	}
	_, _ = diagnostic.Write([]byte(strings.Repeat("x", runnerLogPartialLimit+1)))
	if !b.overflow || len(b.stderrPartial) != 0 {
		t.Fatal("unbounded diagnostic partial line")
	}
	_, _ = diagnostic.Write([]byte("\nrecovered\n"))
	b.finish()
	if !strings.Contains(strings.Join(b.pending, "\n"), "recovered") {
		t.Fatal("diagnostic framing did not recover")
	}
}
