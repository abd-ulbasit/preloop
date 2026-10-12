package cmd

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

// A runner-hosted session (#1482) has no managed agent: the control
// response names send_path and a typed line becomes the next turn there.
func TestAttachCommandModeSendsRunnerSessionTurn(t *testing.T) {
	turnPath := "/api/v1/runner-sessions/" + attachTestSession + "/turns"
	fake := newAttachFake(t)
	fake.extra = func(w http.ResponseWriter, r *http.Request) bool {
		switch {
		case r.URL.Path == runtimeSessionsPath+"/"+attachTestSession+"/control":
			_, _ = io.WriteString(w, `{"runtime_session_id":"`+attachTestSession+`","mode":"command","agent_kind":"runner_session","send_path":"`+turnPath+`"}`)
			return true
		case r.Method == http.MethodPost && r.URL.Path == turnPath:
			body := map[string]interface{}{}
			_ = json.NewDecoder(r.Body).Decode(&body)
			fake.posts = append(fake.posts, attachPost{path: r.URL.Path, body: body})
			w.WriteHeader(http.StatusAccepted)
			_, _ = io.WriteString(w, `{"turn_id":"c0ffee00-0000-4000-8000-000000000002","state":"queued"}`)
			return true
		}
		return false
	}
	fake.conns = []attachConnScript{{}}

	run := startAttach(t, fake, attachOptions{target: attachTestSession})
	run.waitFor(t, "mode: command. A line is the next turn of this runner session")
	_, _ = io.WriteString(run.input, "now run the tests\n")
	run.waitFor(t, "turn c0ffee00 queued")
	_ = run.stop(t)

	posts := fake.postsCopy()
	if len(posts) != 1 || posts[0].path != turnPath || posts[0].body["text"] != "now run the tests" {
		t.Fatalf("a typed line must be one turn POST, got %+v", posts)
	}
}

func TestAttachControlRefusesForeignSendPath(t *testing.T) {
	for _, path := range []string{
		"https://evil.example.com/api/v1/runner-sessions/x/turns",
		"/api/v1/agents/" + attachTestAgent + "/control/prompts",
		"/api/v1/runner-sessions/../../users/me/turns",
	} {
		control := attachControl{Mode: attachModeCommand, SendPath: path}
		if control.isCommand() || control.runnerTurnPath() != "" {
			t.Fatalf("send_path %q must not enable command mode", path)
		}
	}
}

func runnerLive(activityType, status, summary, tool string, metadata string) string {
	return `{"topic":"runtime_sessions","type":"runtime_session_updated","runtime_session_id":"` + attachTestSession +
		`","payload":{"runtime_session_id":"` + attachTestSession + `","session_source_type":"runner_session","last_activity_at":"2026-10-12T12:00:05+00:00","activity_type":"` +
		activityType + `","status":"` + status + `","summary":"` + summary + `","tool_name":"` + tool + `","server_name":"copilot_cli","metadata":` + metadata + `}}`
}

// Runner sessions publish their timeline live (#1482): the turn's progress,
// the agent's reply, its tool calls and the end, after which attach exits.
func TestAttachFollowsRunnerSessionTurnAndExitsOnEnd(t *testing.T) {
	turn := `{"turn_id":"c8092f26-0000-4000-8000-000000000001"}`
	ended := `{"topic":"runtime_sessions","type":"runtime_session_ended","runtime_session_id":"` + attachTestSession + `","payload":{"runtime_session_id":"` + attachTestSession + `","session_source_type":"runner_session","ended_at":"2026-10-12T12:01:00+00:00","end_reason":"stopped_by_actor","state":"ended"}}`
	fake := newAttachFake(t)
	fake.conns = []attachConnScript{{messages: []string{
		runnerLive("operator_turn", "queued", "fix the bug", "", turn),
		runnerLive("operator_turn", "started", "Turn started", "", turn),
		runnerLive("agent_message", "", "Looking at the parser.", "", `{"turn_id":"c8092f26-0000-4000-8000-000000000001","seq":0}`),
		runnerLive("tool_call", "requested", "", "edit", `{"turn_id":"c8092f26-0000-4000-8000-000000000001","seq":1}`),
		runnerLive("tool_result", "completed", "", "", `{"turn_id":"c8092f26-0000-4000-8000-000000000001","seq":2}`),
		runnerLive("usage", "", "", "", `{"turn_id":"c8092f26-0000-4000-8000-000000000001","seq":3}`),
		runnerLive("turn_done", "ok", "", "", `{"turn_id":"c8092f26-0000-4000-8000-000000000001","usage":{"premium_requests":1}}`),
		runnerLive("session_state", "ended", "Ended: stopped_by_actor", "", `{}`),
		ended,
	}}}

	run := startAttach(t, fake, attachOptions{target: attachTestSession, readOnly: true})
	select {
	case err := <-run.done:
		if err != nil {
			t.Fatalf("a runner session ending is not an error: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatalf("attach must exit when the runner session ends:\n%s", run.out.String())
	}
	out := run.out.String()
	for _, want := range []string{
		"turn c8092f26 queued: fix the bug",
		"turn c8092f26 started",
		"from the agent: Looking at the parser.",
		"copilot_cli/edit  requested",
		"turn c8092f26 finished",
		"session ended",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("missing %q in:\n%s", want, out)
		}
	}
	if strings.Count(out, "session ended") != 2 || strings.Contains(out, "usage") {
		// "session ended" once as the event, once as the detach notice.
		t.Errorf("end printed once, usage rows hidden:\n%s", out)
	}
}

func TestAttachReplaysRunnerTurnFailure(t *testing.T) {
	raw := json.RawMessage(`{"activity_type":"turn_done","timestamp":"2026-10-12T12:00:05Z","status":"error","summary":"harness_crashed","metadata":{"turn_id":"c8092f26-0000-4000-8000-000000000001"}}`)
	event, ok := attachEventFromActivity(raw)
	if !ok || event.text != "turn c8092f26 failed (harness_crashed)" || event.hidden {
		t.Fatalf("got %+v", event)
	}
}
