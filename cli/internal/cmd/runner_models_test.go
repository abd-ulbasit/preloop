package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestRunnerModelURL(t *testing.T) {
	for _, value := range []string{"http://localhost:11434/v1", "http://127.0.0.1:1234/v1/", "http://[::1]:8000/v1"} {
		if _, err := normalizeRunnerModelURL(value); err != nil {
			t.Errorf("valid %s: %v", value, err)
		}
	}
	for _, value := range []string{"http://example.com:8000/v1", "http://192.168.1.1:8000/v1", "http://localhost/v1", "http://user@localhost:8000/v1", "http://localhost:8000/v1?x=1", "http://localhost:8000/v1#x", "http://127.1:8000/v1", "https://localhost:8000/v1", "http://localhost:8000/%76%31"} {
		if _, err := normalizeRunnerModelURL(value); err == nil {
			t.Errorf("accepted %s", value)
		}
	}
}

func nextModelFrame(t *testing.T, proxy *runnerModelProxy) map[string]any {
	t.Helper()
	select {
	case frame := <-proxy.frames:
		return frame
	case <-time.After(3 * time.Second):
		t.Fatal("no proxy frame")
		return nil
	}
}

func TestRunnerModelAllowlist(t *testing.T) {
	proxy := newRunnerModelProxy(nil)
	defer proxy.cancel()
	proxy.handle(runnerModelRequest{RequestID: "one", Operation: "start", ModelID: "unknown", BaseURL: "http://localhost:11434/v1", Body: json.RawMessage(`{"model":"example"}`)})
	if frame := nextModelFrame(t, proxy); frame["error"] != "runner_model_not_allowed" {
		t.Fatalf("%v", frame)
	}
}

func TestRunnerModelStreamPullAndCancellation(t *testing.T) {
	cancelled := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		if r.URL.Path != "/v1/chat/completions" || r.Method != "POST" {
			t.Errorf("unexpected %s %s", r.Method, r.URL)
		}
		if r.Header.Get("Authorization") != "" {
			t.Error("forwarded authorization")
		}
		fmt.Fprint(w, "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\ndata: second\n")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
		close(cancelled)
	}))
	defer server.Close()
	proxy := newRunnerModelProxy([]runnerLocalModel{{ID: "model-1", BaseURL: server.URL + "/v1", Model: "example"}})
	defer proxy.cancel()
	request := runnerModelRequest{RequestID: "one", Operation: "start", ModelID: "model-1", BaseURL: server.URL + "/v1", Body: json.RawMessage(`{"model":"example","stream":true}`)}
	proxy.handle(request)
	first := nextModelFrame(t, proxy)
	if !strings.Contains(first["data"].(string), "Hello") {
		t.Fatalf("%v", first)
	}
	select {
	case extra := <-proxy.frames:
		t.Fatalf("unsolicited frame: %v", extra)
	case <-time.After(50 * time.Millisecond):
	}
	proxy.handle(runnerModelRequest{RequestID: "one", Operation: "next"})
	if frame := nextModelFrame(t, proxy); frame["data"] != "" {
		t.Fatalf("expected blank SSE separator: %v", frame)
	}
	proxy.handle(runnerModelRequest{RequestID: "one", Operation: "cancel"})
	select {
	case <-cancelled:
	case <-time.After(3 * time.Second):
		t.Fatal("upstream was not cancelled")
	}
}

func TestRunnerModelRejectsRedirectAndOversize(t *testing.T) {
	for _, mode := range []string{"redirect", "oversize"} {
		t.Run(mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if mode == "redirect" {
					http.Redirect(w, r, "http://example.com/", 302)
					return
				}
				fmt.Fprint(w, strings.Repeat("x", runnerModelFrameLimit+1))
			}))
			defer server.Close()
			proxy := newRunnerModelProxy([]runnerLocalModel{{ID: "model-1", BaseURL: server.URL + "/v1", Model: "example"}})
			defer proxy.cancel()
			proxy.handle(runnerModelRequest{RequestID: "one", Operation: "start", ModelID: "model-1", BaseURL: server.URL + "/v1", Body: json.RawMessage(`{"model":"example"}`)})
			if frame := nextModelFrame(t, proxy); frame["error"] == nil {
				t.Fatalf("expected error: %v", frame)
			}
		})
	}
}

func TestRunnerModelDiscovery(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/models" {
			t.Errorf("path: %s", r.URL.Path)
		}
		fmt.Fprint(w, `{"data":[{"id":"example-model"},{"id":"\u001b[31munsafe"}]}`)
	}))
	defer server.Close()
	models, err := discoverRunnerModelsAt([]string{server.URL + "/v1", server.URL + "/v1"})
	if err != nil {
		t.Fatal(err)
	}
	count := 0
	for _, model := range models {
		if strings.Contains(model.Model, "unsafe") {
			t.Fatal("discovery accepted terminal controls")
		}
		if model.BaseURL == server.URL+"/v1" && model.Model == "example-model" {
			count++
		}
	}
	if count != 1 {
		t.Fatalf("wanted one model, got %d", count)
	}
}

func TestRunnerModelStateIsIsolatedAndPreservesRegistrations(t *testing.T) {
	dir := t.TempDir()
	t.Setenv("PRELOOP_RUNNER_STATE_DIR", dir)
	original := &runnerState{ID: "example-runner", Token: "example-token", LocalModels: []runnerLocalModel{{ID: "model-1", BaseURL: "http://localhost:11434/v1", Model: "example"}}}
	if err := writeRunnerState(original); err != nil {
		t.Fatal(err)
	}
	if err := updateRunnerState(func(state *runnerState) error { state.Name = "Renamed"; return nil }); err != nil {
		t.Fatal(err)
	}
	state, err := readRunnerState()
	if err != nil || len(state.LocalModels) != 1 || state.LocalModels[0] != original.LocalModels[0] {
		t.Fatalf("registration lost: %v %v", state, err)
	}
	path, err := runnerStatePath()
	if err != nil || !strings.HasPrefix(path, dir) {
		t.Fatalf("state escaped private directory: %s %v", path, err)
	}
}

func TestRunnerModelRequestDecodesOnSharedSocket(t *testing.T) {
	var message runnerWSMessage
	err := json.Unmarshal([]byte(`{"type":"model_request","request_id":"one","operation":"start","model_id":"model-1","base_url":"http://localhost:11434/v1","body":{"model":"example","stream":true}}`), &message)
	if err != nil {
		t.Fatal(err)
	}
	if message.Type != "model_request" || message.Operation != "start" || message.ModelID != "model-1" || !strings.Contains(string(message.Body), "example") {
		t.Fatalf("bad shared socket decode: %+v", message)
	}
}

func TestRunnerModelConcurrencyIsBounded(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		io.Copy(io.Discard, r.Body)
		<-r.Context().Done()
	}))
	defer server.Close()
	proxy := newRunnerModelProxy([]runnerLocalModel{{ID: "model-1", BaseURL: server.URL + "/v1", Model: "example"}})
	defer proxy.cancel()
	for i := 0; i < 5; i++ {
		proxy.handle(runnerModelRequest{RequestID: fmt.Sprint(i), Operation: "start", ModelID: "model-1", BaseURL: server.URL + "/v1", Body: json.RawMessage(`{"model":"example"}`)})
	}
	frame := nextModelFrame(t, proxy)
	if frame["error"] != "runner_busy" || frame["status"] != 429 {
		t.Fatalf("%v", frame)
	}
}
