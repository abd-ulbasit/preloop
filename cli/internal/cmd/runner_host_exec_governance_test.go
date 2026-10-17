package cmd

import (
	"bytes"
	"io"
	"os"
	"os/exec"
	"sync"
	"testing"
	"time"
)

type governanceTestPipe struct {
	mu     sync.Mutex
	data   bytes.Buffer
	closed bool
}

func (p *governanceTestPipe) Write(b []byte) (int, error) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return 0, io.ErrClosedPipe
	}
	return p.data.Write(b)
}
func (p *governanceTestPipe) Close() error {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.closed = true
	return nil
}

func TestGovernanceGateWithholdsPromptAndEOFAfterWrongNonce(t *testing.T) {
	pipe := &governanceTestPipe{}
	gate := &hostExecGovernanceGate{nonce: "expected", prompt: "task", stdin: pipe, ready: make(chan struct{}), done: make(chan struct{}), timeout: time.Minute}
	done := make(chan struct{})
	go func() { gate.deliver(&exec.Cmd{}); close(done) }()
	gate.observe(`{"type":"preloop_governance_ready","nonce":"wrong"}`)
	pipe.mu.Lock()
	if pipe.closed || pipe.data.Len() != 0 {
		t.Fatal("released task before readiness")
	}
	pipe.mu.Unlock()
	gate.observe(`{"type":"preloop_governance_ready","nonce":"expected"}`)
	select {
	case <-done:
	case <-time.After(time.Second):
		t.Fatal("task not released")
	}
	pipe.mu.Lock()
	defer pipe.mu.Unlock()
	if pipe.data.String() != "task" || !pipe.closed {
		t.Fatal("task not delivered after readiness")
	}
}

func TestGovernanceReadinessTimeoutKillsNativeChildWithoutTask(t *testing.T) {
	child := exec.Command(os.Args[0], "-test.run=^TestNativeProtocolChildProcess$")
	child.Env = append(os.Environ(), "PRELOOP_PROTOCOL_TEST_CHILD=wait")
	child.SysProcAttr = hostExecSysProcAttr()
	gate, err := newHostExecGovernanceGate(child, "must never arrive", "nonce")
	if err != nil {
		t.Fatal(err)
	}
	gate.timeout = 50 * time.Millisecond
	if err := child.Start(); err != nil {
		t.Fatal(err)
	}
	defer killRunnerJobProcess(child)
	defer gate.close()
	go gate.deliver(child)
	done := make(chan error, 1)
	go func() { done <- child.Wait() }()
	select {
	case err := <-done:
		if err == nil || !gate.failed.Load() {
			t.Fatal("readiness failure accepted")
		}
	case <-time.After(10 * time.Second):
		t.Fatal("readiness timeout did not kill process")
	}
}
