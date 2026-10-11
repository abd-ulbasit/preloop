package cmd

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// fakeViewAPI serves the REST routes and the viewer socket used by
// `preloop agents desktop`.
type fakeViewAPI struct {
	// holdOpen keeps the viewer socket open until the client closes it.
	holdOpen bool
	agents   []map[string]string
	gets     []string
	server   *httptest.Server
	mu       sync.Mutex
	deleted  []string
	created  []map[string]interface{}
	wsAuth   string
	received []byte
	got      chan struct{}
}

func newFakeViewAPI(t *testing.T) *fakeViewAPI {
	return newFakeViewAPIWith(t, false, nil)
}

func newFakeViewAPIWith(t *testing.T, holdOpen bool, agents []map[string]string) *fakeViewAPI {
	if agents == nil {
		agents = []map[string]string{
			{"id": "11111111-aaaa", "display_name": "Browser agent"},
			{"id": "22222222-bbbb", "display_name": "Other"},
		}
	}
	api := &fakeViewAPI{got: make(chan struct{}, 1), holdOpen: holdOpen, agents: agents}
	upgrader := websocket.Upgrader{Subprotocols: []string{"binary"}}
	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/agents", func(w http.ResponseWriter, r *http.Request) {
		api.mu.Lock()
		api.gets = append(api.gets, r.URL.RawQuery)
		api.mu.Unlock()
		offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		end := offset + limit
		if end > len(api.agents) {
			end = len(api.agents)
		}
		page := []map[string]string{}
		if offset < len(api.agents) {
			page = api.agents[offset:end]
		}
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"items": page})
	})
	mux.HandleFunc("/api/v1/agents/", func(w http.ResponseWriter, r *http.Request) {
		id := strings.TrimPrefix(r.URL.Path, "/api/v1/agents/")
		if strings.Contains(id, "/") {
			http.NotFound(w, r)
			return
		}
		for _, agent := range api.agents {
			if agent["id"] == id {
				_ = json.NewEncoder(w).Encode(agent)
				return
			}
		}
		http.NotFound(w, r)
	})
	mux.HandleFunc("/api/v1/agents/11111111-aaaa/desktop-views", func(w http.ResponseWriter, r *http.Request) {
		var body map[string]interface{}
		_ = json.NewDecoder(r.Body).Decode(&body)
		api.mu.Lock()
		api.created = append(api.created, body)
		api.mu.Unlock()
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(map[string]string{
			"view_id": "view-1", "ws_url": "/api/v1/ws/desktop/view-1",
			"expires_at": "2026-10-11T12:15:00Z", "mode": "view_only", "state": "pending",
		})
	})
	mux.HandleFunc("/api/v1/desktop/views/view-1", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodDelete {
			api.mu.Lock()
			api.deleted = append(api.deleted, "view-1")
			api.mu.Unlock()
			w.WriteHeader(http.StatusNoContent)
		}
	})
	mux.HandleFunc("/api/v1/ws/desktop/view-1", func(w http.ResponseWriter, r *http.Request) {
		api.mu.Lock()
		api.wsAuth = r.Header.Get("Authorization")
		api.mu.Unlock()
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close() //nolint:errcheck
		_ = conn.WriteMessage(websocket.BinaryMessage, []byte(rfbVersion38))
		if api.holdOpen {
			for {
				if _, _, err := conn.ReadMessage(); err != nil {
					api.got <- struct{}{}
					return
				}
			}
		}
		_, data, err := conn.ReadMessage()
		if err == nil {
			api.mu.Lock()
			api.received = data
			api.mu.Unlock()
		}
		api.got <- struct{}{}
		_ = conn.WriteControl(websocket.CloseMessage, websocket.FormatCloseMessage(4411, "expired"), time.Now().Add(time.Second))
		time.Sleep(100 * time.Millisecond)
	})
	api.server = httptest.NewServer(mux)
	t.Cleanup(api.server.Close)
	return api
}

type fakeHTTPClient struct {
	base  string
	token string
}

func (c *fakeHTTPClient) do(method, path string, body, result interface{}) error {
	var reader io.Reader
	if body != nil {
		data, _ := json.Marshal(body)
		reader = bytes.NewReader(data)
	}
	req, _ := http.NewRequest(method, c.base+path, reader)
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close() //nolint:errcheck
	if resp.StatusCode >= 300 {
		return errors.New(resp.Status)
	}
	if result != nil {
		return json.NewDecoder(resp.Body).Decode(result)
	}
	return nil
}

func (c *fakeHTTPClient) Get(path string, result interface{}) error {
	return c.do(http.MethodGet, path, nil, result)
}
func (c *fakeHTTPClient) Post(path string, body, result interface{}) error {
	return c.do(http.MethodPost, path, body, result)
}
func (c *fakeHTTPClient) Delete(path string, result interface{}) error {
	return c.do(http.MethodDelete, path, nil, result)
}
func (c *fakeHTTPClient) Token() string   { return c.token }
func (c *fakeHTTPClient) BaseURL() string { return c.base }

func TestAgentsDesktopRelaysLocalVNCClient(t *testing.T) {
	api := newFakeViewAPI(t)
	out := &lockedWriter{w: &bytes.Buffer{}}
	ready := make(chan string, 1)
	done := make(chan error, 1)
	go func() {
		done <- runAgentsDesktop(context.Background(), desktopViewSession{
			client:   &fakeHTTPClient{base: api.server.URL, token: "header.jwt.sig"},
			agent:    "browser agent",
			port:     0,
			duration: 5 * time.Minute,
			out:      out,
			dial:     defaultDesktopWSDialer,
			ready:    ready,
		})
	}()
	addr := <-ready
	if host, _, _ := net.SplitHostPort(addr); host != "127.0.0.1" {
		t.Fatalf("listener on %s, want loopback", addr)
	}
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close() //nolint:errcheck
	version := make([]byte, 12)
	if _, err := io.ReadFull(conn, version); err != nil || string(version) != rfbVersion38 {
		t.Fatalf("version = %q %v", version, err)
	}
	_, _ = conn.Write([]byte(rfbVersion38))
	<-api.got
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("agents desktop did not end when the view closed")
	}
	api.mu.Lock()
	defer api.mu.Unlock()
	if string(api.received) != rfbVersion38 {
		t.Fatalf("server got %q", api.received)
	}
	if api.wsAuth != "Bearer header.jwt.sig" {
		t.Fatalf("ws auth = %q", api.wsAuth)
	}
	if len(api.created) != 1 || api.created[0]["duration_seconds"] != float64(300) {
		t.Fatalf("created = %v", api.created)
	}
	if len(api.deleted) != 1 {
		t.Fatal("view not deleted on exit")
	}
	if !strings.Contains(out.String(), "view only") || !strings.Contains(out.String(), "expired") {
		t.Fatalf("output:\n%s", out.String())
	}
}

func TestResolveDesktopAgentID(t *testing.T) {
	api := newFakeViewAPI(t)
	client := &fakeHTTPClient{base: api.server.URL, token: "t"}
	for in, want := range map[string]string{
		"11111111-aaaa": "11111111-aaaa",
		"Browser Agent": "11111111-aaaa",
		"2222":          "22222222-bbbb",
	} {
		if got, err := resolveDesktopAgentID(client, in); err != nil || got != want {
			t.Errorf("%q -> %q %v", in, got, err)
		}
	}
	if _, err := resolveDesktopAgentID(client, "nobody"); err == nil {
		t.Error("unknown agent resolved")
	}
}

func TestAgentsDesktopLocalClientDisconnectsFirst(t *testing.T) {
	// The common way a view ends: the VNC client closes while the viewer
	// socket is still open. Run with -race: the close code must not be read
	// while the other pump is still writing it.
	api := newFakeViewAPIWith(t, true, nil)
	ready := make(chan string, 1)
	done := make(chan error, 1)
	go func() {
		done <- runAgentsDesktop(context.Background(), desktopViewSession{
			client: &fakeHTTPClient{base: api.server.URL, token: "header.jwt.sig"},
			agent:  "Browser agent",
			out:    &lockedWriter{w: &bytes.Buffer{}},
			dial:   defaultDesktopWSDialer,
			ready:  ready,
		})
	}()
	conn, err := net.Dial("tcp", <-ready)
	if err != nil {
		t.Fatal(err)
	}
	version := make([]byte, 12)
	if _, err := io.ReadFull(conn, version); err != nil {
		t.Fatal(err)
	}
	_ = conn.Close()
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("agents desktop did not end when the VNC client left")
	}
	<-api.got
	api.mu.Lock()
	defer api.mu.Unlock()
	if len(api.deleted) != 1 {
		t.Fatal("view not deleted")
	}
}

func TestResolveDesktopAgentIDPagesAndLooksUpIDs(t *testing.T) {
	var agents []map[string]string
	for i := 0; i < 150; i++ {
		agents = append(agents, map[string]string{
			"id":           fmt.Sprintf("00000000-0000-4000-8000-%012d", i),
			"display_name": fmt.Sprintf("agent %d", i),
		})
	}
	api := newFakeViewAPIWith(t, false, agents)
	client := &fakeHTTPClient{base: api.server.URL, token: "t"}
	got, err := resolveDesktopAgentID(client, "agent 142")
	if err != nil || got != agents[142]["id"] {
		t.Fatalf("name on page 2: %q %v", got, err)
	}
	got, err = resolveDesktopAgentID(client, agents[149]["id"])
	if err != nil || got != agents[149]["id"] {
		t.Fatalf("exact id beyond 100: %q %v", got, err)
	}
	if _, err := resolveDesktopAgentID(client, "00000000-0000-4000-8000-999999999999"); err == nil {
		t.Fatal("unknown uuid resolved")
	}
	api.mu.Lock()
	defer api.mu.Unlock()
	if len(api.gets) != 2 || api.gets[1] != "limit=100&offset=100" {
		t.Fatalf("list calls = %v", api.gets)
	}
}
