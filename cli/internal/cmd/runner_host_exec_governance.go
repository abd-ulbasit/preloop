package cmd

import (
	"encoding/json"
	"io"
	"os/exec"
	"sync"
	"sync/atomic"
	"time"
)

// DeepSeek's optional-plugin loader may continue after activation failure.
// Its headless runner awaits stdin EOF before creating an agent. Withhold
// both task and EOF until our wrapper acknowledges that tool guards exist.
type hostExecGovernanceGate struct {
	nonce     string
	prompt    string
	stdin     io.WriteCloser
	ready     chan struct{}
	done      chan struct{}
	delivered chan struct{}
	readyOnce sync.Once
	doneOnce  sync.Once
	failed    atomic.Bool
	readySeen atomic.Bool
	timeout   time.Duration
}

func newHostExecGovernanceGate(cmd *exec.Cmd, prompt, nonce string) (*hostExecGovernanceGate, error) {
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	return &hostExecGovernanceGate{nonce: nonce, prompt: prompt, stdin: stdin, ready: make(chan struct{}), done: make(chan struct{}), delivered: make(chan struct{}), timeout: 15 * time.Second}, nil
}

func (g *hostExecGovernanceGate) observe(line string) bool {
	var e struct {
		Type  string `json:"type"`
		Nonce string `json:"nonce"`
	}
	if json.Unmarshal([]byte(line), &e) != nil {
		return false
	}
	if e.Type != "preloop_governance_ready" {
		if !g.readySeen.Load() && (e.Type == "session" || e.Type == "turn.started" || e.Type == "step_start") {
			g.failed.Store(true)
		}
		return false
	}
	if e.Nonce == g.nonce {
		g.readySeen.Store(true)
		g.readyOnce.Do(func() { close(g.ready) })
	}
	return true // Internal acknowledgement never enters user logs.
}

func (g *hostExecGovernanceGate) deliver(cmd *exec.Cmd) {
	if g.delivered != nil {
		defer close(g.delivered)
	}
	timer := time.NewTimer(g.timeout)
	defer timer.Stop()
	select {
	case <-g.ready:
		select {
		case <-g.done:
			return
		default:
		}
		if g.failed.Load() {
			killRunnerJobProcess(cmd)
			_ = g.stdin.Close()
			return
		}
		if _, err := io.WriteString(g.stdin, g.prompt); err != nil {
			g.failed.Store(true)
			killRunnerJobProcess(cmd)
		}
		_ = g.stdin.Close()
	case <-g.done:
	case <-timer.C:
		g.failed.Store(true)
		// Kill before closing stdin: an EOF alone could start an ungoverned run.
		killRunnerJobProcess(cmd)
		_ = g.stdin.Close()
	}
}

func (g *hostExecGovernanceGate) close() {
	g.doneOnce.Do(func() { close(g.done) })
	_ = g.stdin.Close()
}
