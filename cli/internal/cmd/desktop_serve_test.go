package cmd

import (
	"bytes"
	"context"
	"crypto/des"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

const testVNCPassword = "s3cretpw"

func TestVNCObfuscationKeyMatchesReference(t *testing.T) {
	// The d3des key schedule reverses key bits; in standard DES terms the
	// vncpasswd obfuscation key is e84ad660c4721ae0.
	if got := hex.EncodeToString(vncDESKey(vncPasswdObfuscationKey)); got != "e84ad660c4721ae0" {
		t.Fatalf("obfuscation key = %s", got)
	}
}

func TestVNCPasswordFileRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "vncpasswd")
	if err := os.WriteFile(path, encodeVNCPasswordFile([]byte(testVNCPassword)), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := readVNCPasswordFile(path)
	if err != nil || string(got) != testVNCPassword {
		t.Fatalf("got %q, %v", got, err)
	}
	if bytes.Contains(encodeVNCPasswordFile([]byte(testVNCPassword)), []byte(testVNCPassword)) {
		t.Fatal("stored password must not be plaintext")
	}
}

// fakeVNC accepts one RFB 3.8 client that must pass VNC auth.
type fakeVNC struct {
	ln       net.Listener
	mu       sync.Mutex
	authOK   bool
	received []byte
}

func startFakeVNC(t *testing.T) *fakeVNC {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	f := &fakeVNC{ln: ln}
	go func() {
		for {
			conn, err := ln.Accept()
			if err != nil {
				return
			}
			go f.handle(conn)
		}
	}()
	t.Cleanup(func() { _ = ln.Close() })
	return f
}

func (f *fakeVNC) handle(conn net.Conn) {
	defer conn.Close() //nolint:errcheck
	_, _ = conn.Write([]byte(rfbVersion38))
	version := make([]byte, 12)
	if _, err := io.ReadFull(conn, version); err != nil {
		return
	}
	_, _ = conn.Write([]byte{1, rfbSecurityVNCAuth})
	chosen := make([]byte, 1)
	if _, err := io.ReadFull(conn, chosen); err != nil || chosen[0] != rfbSecurityVNCAuth {
		return
	}
	challenge := []byte("0123456789abcdef")
	_, _ = conn.Write(challenge)
	response := make([]byte, 16)
	if _, err := io.ReadFull(conn, response); err != nil {
		return
	}
	want, _ := vncAuthResponse([]byte(testVNCPassword), challenge)
	if !bytes.Equal(response, want) {
		_ = binary.Write(conn, binary.BigEndian, uint32(1))
		return
	}
	f.mu.Lock()
	f.authOK = true
	f.mu.Unlock()
	_ = binary.Write(conn, binary.BigEndian, uint32(0))
	clientInit := make([]byte, 1)
	if _, err := io.ReadFull(conn, clientInit); err != nil {
		return
	}
	init := make([]byte, 0, 32)
	init = binary.BigEndian.AppendUint16(init, 2)
	init = binary.BigEndian.AppendUint16(init, 2)
	init = append(init, make([]byte, 16)...)
	init = binary.BigEndian.AppendUint32(init, 4)
	init = append(init, []byte("fake")...)
	_, _ = conn.Write(init)
	buf := make([]byte, 4096)
	for {
		n, err := conn.Read(buf)
		if err != nil {
			return
		}
		f.mu.Lock()
		f.received = append(f.received, buf[:n]...)
		f.mu.Unlock()
		if buf[0] == 3 {
			_, _ = conn.Write([]byte{0, 0, 0, 1, 0, 0, 0, 0, 0, 2, 0, 2, 0, 0, 0, 0})
			_, _ = conn.Write(bytes.Repeat([]byte{0xff, 0, 0, 0}, 4))
		}
	}
}

// fakeDesktopAPI plays the Preloop API side of both endpoint sockets and
// acts as the RFB viewer on the view socket. It records every frame the
// endpoint sends so tests can assert what leaves the host.
type fakeDesktopAPI struct {
	t         *testing.T
	server    *httptest.Server
	mu        sync.Mutex
	frames    [][]byte
	register  map[string]interface{}
	headers   []http.Header
	openError chan map[string]interface{}
	viewDone  chan []byte
}

func newFakeDesktopAPI(t *testing.T) *fakeDesktopAPI {
	api := &fakeDesktopAPI{
		t:         t,
		openError: make(chan map[string]interface{}, 1),
		viewDone:  make(chan []byte, 1),
	}
	upgrader := websocket.Upgrader{}
	mux := http.NewServeMux()
	mux.HandleFunc(desktopEndpointsWSPath, func(w http.ResponseWriter, r *http.Request) {
		api.record(nil, r.Header)
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close() //nolint:errcheck
		_, raw, err := conn.ReadMessage()
		if err != nil {
			return
		}
		api.record(raw, nil)
		api.mu.Lock()
		_ = json.Unmarshal(raw, &api.register)
		endpointID, _ := api.register["endpoint_id"].(string)
		api.mu.Unlock()
		_ = conn.WriteJSON(map[string]interface{}{
			"type": "desktop.registered", "id": "pk", "endpoint_id": endpointID,
			"heartbeat_interval_seconds": 1,
		})
		_ = conn.WriteJSON(map[string]interface{}{
			"type": "desktop.open", "view_id": "view-1", "view_token": "tok-1",
			"expires_at": "2026-10-11T12:00:00Z", "mode": "view_only",
		})
		for {
			_, raw, err := conn.ReadMessage()
			if err != nil {
				return
			}
			api.record(raw, nil)
			var msg map[string]interface{}
			_ = json.Unmarshal(raw, &msg)
			if msg["type"] == "desktop.open_result" {
				api.openError <- msg
			}
		}
	})
	mux.HandleFunc("/api/v1/desktop/endpoints/", func(w http.ResponseWriter, r *http.Request) {
		api.record(nil, r.Header)
		if r.Header.Get(desktopViewTokenHeader) != "tok-1" {
			http.Error(w, "bad token", http.StatusUnauthorized)
			return
		}
		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			return
		}
		defer conn.Close() //nolint:errcheck
		stream := &recordingStream{api: api, ws: newWSStream(conn)}
		frame, err := runFakeViewer(stream)
		if err != nil {
			t.Errorf("viewer: %v", err)
		}
		api.viewDone <- frame
	})
	api.server = httptest.NewServer(mux)
	t.Cleanup(api.server.Close)
	return api
}

func (a *fakeDesktopAPI) record(frame []byte, header http.Header) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if frame != nil {
		a.frames = append(a.frames, append([]byte(nil), frame...))
	}
	if header != nil {
		a.headers = append(a.headers, header.Clone())
	}
}

type recordingStream struct {
	api *fakeDesktopAPI
	ws  *wsStream
}

func (r *recordingStream) Read(p []byte) (int, error) {
	n, err := r.ws.Read(p)
	r.api.record(p[:n], nil)
	return n, err
}

func (r *recordingStream) Write(p []byte) (int, error) { return r.ws.Write(p) }

func runFakeViewer(rw io.ReadWriter) ([]byte, error) {
	version := make([]byte, 12)
	if _, err := io.ReadFull(rw, version); err != nil || string(version) != rfbVersion38 {
		return nil, errors.New("bad version")
	}
	_, _ = rw.Write([]byte(rfbVersion38))
	types := make([]byte, 2)
	if _, err := io.ReadFull(rw, types); err != nil || types[0] != 1 || types[1] != rfbSecurityNone {
		return nil, errors.New("expected only security None")
	}
	_, _ = rw.Write([]byte{rfbSecurityNone})
	result := make([]byte, 4)
	if _, err := io.ReadFull(rw, result); err != nil {
		return nil, err
	}
	_, _ = rw.Write([]byte{1})
	init := make([]byte, 24+4)
	if _, err := io.ReadFull(rw, init); err != nil {
		return nil, err
	}
	_, _ = rw.Write([]byte{3, 0, 0, 0, 0, 0, 0, 2, 0, 2})
	frame := make([]byte, 16+16)
	if _, err := io.ReadFull(rw, frame); err != nil {
		return nil, err
	}
	return frame[16:], nil
}

func serveForTest(t *testing.T, api *fakeDesktopAPI, opts desktopServeOptions) (*lockedWriter, context.CancelFunc, chan error) {
	t.Helper()
	out := &lockedWriter{w: &bytes.Buffer{}}
	home := t.TempDir()
	passwd := filepath.Join(home, "vncpasswd")
	if err := os.WriteFile(passwd, encodeVNCPasswordFile([]byte(testVNCPassword)), 0o600); err != nil {
		t.Fatal(err)
	}
	opts.BaseURL = api.server.URL
	opts.Token = "pk-agent-key"
	opts.PasswdFile = passwd
	opts.StatePath = filepath.Join(home, "desktop.json")
	opts.Hostname = "host.example.com"
	opts.Out = out
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- runDesktopServe(ctx, opts) }()
	t.Cleanup(cancel)
	return out, cancel, done
}

type lockedWriter struct {
	mu sync.Mutex
	w  io.Writer
}

func (l *lockedWriter) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.w.(*bytes.Buffer).String()
}

func (l *lockedWriter) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.w.Write(p)
}

func TestDesktopServeRelaysAndKeepsVNCPasswordLocal(t *testing.T) {
	vnc := startFakeVNC(t)
	api := newFakeDesktopAPI(t)
	out, cancel, done := serveForTest(t, api, desktopServeOptions{VNCAddr: vnc.ln.Addr().String()})

	select {
	case frame := <-api.viewDone:
		if !bytes.Equal(frame, bytes.Repeat([]byte{0xff, 0, 0, 0}, 4)) {
			t.Fatalf("frame = %x", frame)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("view never relayed a frame")
	}
	cancel()
	<-done

	vnc.mu.Lock()
	authOK := vnc.authOK
	vnc.mu.Unlock()
	if !authOK {
		t.Fatal("endpoint did not authenticate to the VNC server")
	}
	api.mu.Lock()
	defer api.mu.Unlock()
	if api.register["type"] != "desktop.register" || api.register["hostname"] != "host.example.com" {
		t.Fatalf("register = %v", api.register)
	}
	endpointID, _ := api.register["endpoint_id"].(string)
	if !strings.HasPrefix(endpointID, "dep_") {
		t.Fatalf("endpoint_id = %q", endpointID)
	}
	encoded := encodeVNCPasswordFile([]byte(testVNCPassword))
	for _, frame := range api.frames {
		if bytes.Contains(frame, []byte(testVNCPassword)) || bytes.Contains(frame, encoded) {
			t.Fatalf("VNC password left the host in frame %q", frame)
		}
	}
	for _, header := range api.headers {
		for _, values := range header {
			for _, v := range values {
				if strings.Contains(v, testVNCPassword) {
					t.Fatal("VNC password left the host in a header")
				}
			}
		}
		if header.Get("Authorization") != "Bearer pk-agent-key" {
			t.Fatalf("auth header = %q", header.Get("Authorization"))
		}
	}
	if strings.Contains(out.String(), testVNCPassword) {
		t.Fatal("VNC password printed")
	}
}

func TestDesktopServeReportsDisplayUnavailable(t *testing.T) {
	api := newFakeDesktopAPI(t)
	serveForTest(t, api, desktopServeOptions{
		VNCAddr: "127.0.0.1:5999",
		DialVNC: func(ctx context.Context, addr string) (net.Conn, error) {
			return nil, errors.New("connection refused")
		},
	})
	select {
	case msg := <-api.openError:
		if msg["ok"] != false || msg["error"] != "display_unavailable" || msg["view_id"] != "view-1" {
			t.Fatalf("open_result = %v", msg)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("no open_result")
	}
}

func TestDesktopServeOptionValidation(t *testing.T) {
	cases := map[string]desktopServeOptions{
		"no token":     {},
		"login token":  {Token: "header.payload.signature"},
		"remote vnc":   {Token: "pk", VNCAddr: "10.0.0.5:5900"},
		"bad vnc addr": {Token: "pk", VNCAddr: "nonsense"},
	}
	for name, opts := range cases {
		if err := opts.defaults(); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
	ok := desktopServeOptions{Token: "pk"}
	if err := ok.defaults(); err != nil || ok.VNCAddr != "127.0.0.1:5900" {
		t.Fatalf("defaults: %v %q", err, ok.VNCAddr)
	}
}

func TestDesktopEndpointIDIsStableAndKeepsManifest(t *testing.T) {
	path := filepath.Join(t.TempDir(), "desktop.json")
	if err := writeDesktopManifest(filepath.Dir(filepath.Dir(path)), "chromium", time.Unix(0, 0)); err != nil {
		t.Fatal(err)
	}
	manifestPath := filepath.Join(filepath.Dir(filepath.Dir(path)), ".preloop", "desktop.json")
	first, err := loadOrCreateDesktopEndpointID(manifestPath)
	if err != nil {
		t.Fatal(err)
	}
	second, err := loadOrCreateDesktopEndpointID(manifestPath)
	if err != nil || first != second {
		t.Fatalf("id changed: %q %q %v", first, second, err)
	}
	data, _ := os.ReadFile(manifestPath)
	var doc map[string]interface{}
	_ = json.Unmarshal(data, &doc)
	if doc["display"] != ":99" || doc["endpoint_id"] != first {
		t.Fatalf("manifest = %s", data)
	}
	info, _ := os.Stat(manifestPath)
	if runtime.GOOS != "windows" && info.Mode().Perm() != 0o600 {
		t.Fatalf("mode = %v", info.Mode()) // Windows has no POSIX modes
	}
	// Re-running the installer keeps the endpoint identity.
	if err := writeDesktopManifest(filepath.Dir(filepath.Dir(path)), "chromium", time.Unix(0, 0)); err != nil {
		t.Fatal(err)
	}
	third, _ := loadOrCreateDesktopEndpointID(manifestPath)
	if third != first {
		t.Fatalf("installer rewrite lost endpoint_id: %q != %q", third, first)
	}
}

func TestDesktopWSURL(t *testing.T) {
	cases := map[string]string{
		"https://preloop.example.com": "wss://preloop.example.com/x",
		"http://localhost:8000/":      "ws://localhost:8000/x",
	}
	for in, want := range cases {
		if got, err := desktopWSURL(in, "/x"); err != nil || got != want {
			t.Errorf("%s -> %s %v", in, got, err)
		}
	}
	if _, err := desktopWSURL("ftp://x", "/x"); err == nil {
		t.Error("ftp accepted")
	}
}

func TestResolveDesktopToken(t *testing.T) {
	home := t.TempDir()
	t.Setenv(desktopTokenEnv, "")
	if _, err := resolveDesktopToken(home, ""); err == nil {
		t.Fatal("expected missing credential error")
	}
	tokenFile := filepath.Join(home, ".preloop", "desktop", "token")
	_ = os.MkdirAll(filepath.Dir(tokenFile), 0o700)
	_ = os.WriteFile(tokenFile, []byte("pk-file\n"), 0o600)
	if got, _ := resolveDesktopToken(home, ""); got != "pk-file" {
		t.Fatalf("file token = %q", got)
	}
	t.Setenv(desktopTokenEnv, "pk-env")
	if got, _ := resolveDesktopToken(home, ""); got != "pk-env" {
		t.Fatalf("env token = %q", got)
	}
}

// encodeVNCPasswordFile is the inverse of readVNCPasswordFile (test fakes only).
func encodeVNCPasswordFile(password []byte) []byte {
	block, _ := des.NewCipher(vncDESKey(vncPasswdObfuscationKey))
	plain := make([]byte, 8)
	copy(plain, password)
	out := make([]byte, 8)
	block.Encrypt(out, plain)
	return out
}
