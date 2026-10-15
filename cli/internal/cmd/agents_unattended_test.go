package cmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/preloop/preloop/cli/internal/api"
	"github.com/preloop/preloop/cli/internal/testenv"
	"github.com/zalando/go-keyring"
)

func TestUnattendedSelectedAppsOnlyAndRerunUnchanged(t *testing.T) {
	home := testenv.SetTempHome(t)
	keyring.MockInit()
	priorMachineID := readMachineID
	readMachineID = func() (string, error) { return "synthetic-workstation", nil }
	t.Cleanup(func() { readMachineID = priorMachineID })
	// A subscription fixture exists, but this mode must leave it byte-exact.
	authPath := filepath.Join(home, ".codex", "auth.json")
	if err := os.MkdirAll(filepath.Dir(authPath), 0700); err != nil {
		t.Fatal(err)
	}
	auth := []byte(`{"tokens":{"refresh_token":"synthetic-personal-lineage"}}`)
	if err := os.WriteFile(authPath, auth, 0600); err != nil {
		t.Fatal(err)
	}
	cursorPath := filepath.Join(home, ".cursor", "mcp.json")
	if err := os.MkdirAll(filepath.Dir(cursorPath), 0700); err != nil {
		t.Fatal(err)
	}
	cursor := []byte(`{"mcpServers":{"example":{"url":"https://example.com/mcp"}}}`)
	if err := os.WriteFile(cursorPath, cursor, 0600); err != nil {
		t.Fatal(err)
	}
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/v1/agents/enrollment/applied" {
			if !strings.HasPrefix(r.Header.Get("Authorization"), "Bearer agt_") {
				t.Error("acknowledgment used provisioning authority")
			}
			w.Header().Set("Content-Type", "application/json")
			_, _ = w.Write([]byte(`{"state":"configured"}`))
			return
		}
		calls++
		if r.Method != "POST" || r.URL.Path != "/api/v1/agents/enrollment/exchange" {
			t.Errorf("unexpected API %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer enr_synthetic" {
			t.Error("wrong authority")
		}
		var body map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(body["apps"], []interface{}{"claude_code", "codex"}) {
			t.Errorf("apps = %#v", body["apps"])
		}
		raw, _ := json.Marshal(body)
		if bytes.Contains(raw, []byte("synthetic-workstation")) || bytes.Contains(raw, []byte("synthetic-mdm")) || bytes.Contains(raw, []byte("personal-lineage")) {
			t.Error("raw identity/credential leaked")
		}
		expected := workstationFingerprint(strings.Repeat("s", 64), "synthetic-workstation")
		if body["workstation_fingerprint"] != expected {
			t.Error("wrong account-scoped fingerprint")
		}
		response := unattendedCheckpoint{SchemaVersion: 1, Apps: []unattendedAppCredential{
			{App: "claude_code", AgentID: "synthetic-agent-1", RuntimePrincipalID: strings.Repeat("a", 64), EnrollmentID: "synthetic-enrollment-1", Token: "agt_synthetic1", ExpiresAt: api.Time{Time: time.Now().Add(time.Hour)}},
			{App: "codex", AgentID: "synthetic-agent-2", RuntimePrincipalID: strings.Repeat("b", 64), EnrollmentID: "synthetic-enrollment-2", Token: "agt_synthetic2", ExpiresAt: api.Time{Time: time.Now().Add(time.Hour)}},
		}}
		_ = json.NewEncoder(w).Encode(response)
	}))
	defer server.Close()
	tokenFile := filepath.Join(home, "authority.json")
	raw, _ := json.Marshal(enrollmentAuthorityFile{SchemaVersion: 1, Token: "enr_synthetic", Salt: strings.Repeat("s", 64), ExpiresAt: time.Now().Add(time.Hour)})
	if err := os.WriteFile(tokenFile, raw, 0600); err != nil {
		t.Fatal(err)
	}
	if err := protectUnattendedConfig(tokenFile); err != nil {
		t.Fatal(err)
	}
	opts := unattendedOptions{URL: server.URL, TokenFile: tokenFile, Apps: []string{"claude_code", "codex"}, DeviceRef: "synthetic-mdm"}
	var out bytes.Buffer
	if err := runUnattendedForUser(opts, &out); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Fatalf("exchange calls = %d", calls)
	}
	paths := []string{filepath.Join(home, ".claude.json"), filepath.Join(home, ".codex", "config.toml")}
	content := map[string][]byte{}
	mtimes := map[string]time.Time{}
	for _, path := range paths {
		content[path], _ = os.ReadFile(path)
		info, err := os.Stat(path)
		if err != nil {
			t.Fatal(err)
		}
		mtimes[path] = info.ModTime()
		if !bytes.Contains(content[path], []byte("agt_synthetic")) {
			t.Errorf("no scoped token in %s", path)
		}
	}
	if err := os.Remove(tokenFile); err != nil {
		t.Fatal(err)
	}
	out.Reset()
	if err := runUnattendedForUser(opts, &out); err != nil {
		t.Fatal(err)
	}
	if calls != 1 {
		t.Error("unchanged rerun contacted server")
	}
	for _, path := range paths {
		after, _ := os.ReadFile(path)
		info, _ := os.Stat(path)
		if !bytes.Equal(content[path], after) || !info.ModTime().Equal(mtimes[path]) {
			t.Errorf("rerun changed %s", path)
		}
	}
	if after, _ := os.ReadFile(authPath); !bytes.Equal(auth, after) {
		t.Error("personal credential changed")
	}
	if after, _ := os.ReadFile(cursorPath); !bytes.Equal(cursor, after) {
		t.Error("unselected Cursor changed")
	}
	if !strings.Contains(out.String(), "unchanged") {
		t.Errorf("output %s", out.String())
	}
}

func TestUnattendedCheckpointRecoversAfterInterruptedLocalApply(t *testing.T) {
	home := testenv.SetTempHome(t)
	keyring.MockInit()
	app := unattendedAppCredential{App: "cursor", AgentID: "synthetic-agent", RuntimePrincipalID: strings.Repeat("a", 64), EnrollmentID: "synthetic-enrollment", Token: "agt_synthetic", ExpiresAt: api.Time{Time: time.Now().Add(time.Hour)}}
	checkpoint := unattendedCheckpoint{SchemaVersion: 1, Apps: []unattendedAppCredential{app}}
	raw, _ := json.Marshal(checkpoint)
	const keyID = "synthetic-recovery"
	if err := keyring.Set(enrollmentKeyringService, keyID, string(raw)); err != nil {
		t.Fatal(err)
	}
	ackCalls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ackCalls++
		if r.URL.Path != "/api/v1/agents/enrollment/applied" {
			t.Errorf("unexpected recovery request %s", r.URL.Path)
		}
		_, _ = w.Write([]byte(`{"state":"configured"}`))
	}))
	defer server.Close()
	opts := unattendedOptions{URL: server.URL, TokenFile: "missing", Apps: []string{"cursor"}}
	if err := applyUnattendedEnrollment(opts, home, "synthetic-user", keyID, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	path := filepath.Join(home, ".cursor", "mcp.json")
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if err := applyUnattendedEnrollment(opts, home, "synthetic-user", keyID, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(path)
	if !bytes.Contains(data, []byte(app.Token)) {
		t.Fatal("checkpoint did not restore config")
	}
	if ackCalls != 1 {
		t.Fatalf("acknowledged unchanged recovery %d times", ackCalls)
	}
}

func TestUnattendedAppSelectionValidation(t *testing.T) {
	for _, apps := range [][]string{nil, {"all"}, {"claude_code", "Claude Code"}, {"OpenClaw"}, {""}} {
		if _, err := normalizeUnattendedApps(apps); err == nil {
			t.Errorf("accepted %#v", apps)
		}
	}
	actual, err := normalizeUnattendedApps([]string{"Cursor", "Claude Code", "Codex CLI"})
	if err != nil || !reflect.DeepEqual(actual, []string{"claude_code", "codex", "cursor"}) {
		t.Fatalf("got %#v %v", actual, err)
	}
}

func TestUnattendedInvalidCheckpointNeverExchanges(t *testing.T) {
	home := testenv.SetTempHome(t)
	keyring.MockInit()
	for i, raw := range []string{`invalid`, `{"schema_version":2,"apps":[]}`, `{"schema_version":1,"apps":[{"app":"cursor","token":"agt_synthetic"}]}`} {
		key := fmt.Sprintf("invalid-%d", i)
		_ = keyring.Set(enrollmentKeyringService, key, raw)
		err := applyUnattendedEnrollment(unattendedOptions{URL: "https://example.com", Apps: []string{"cursor"}}, home, "synthetic", key, &bytes.Buffer{})
		if err == nil {
			t.Error("accepted corrupt checkpoint")
		}
	}
}

func TestUnattendedMachineIdentityFailurePrecedesExchange(t *testing.T) {
	home := testenv.SetTempHome(t)
	keyring.MockInit()
	prior := readMachineID
	readMachineID = func() (string, error) { return "", errors.New("synthetic failure") }
	t.Cleanup(func() { readMachineID = prior })
	path := filepath.Join(home, "authority.json")
	raw, _ := json.Marshal(enrollmentAuthorityFile{SchemaVersion: 1, Token: "enr_synthetic", Salt: strings.Repeat("s", 64), ExpiresAt: time.Now().Add(time.Hour)})
	_ = os.WriteFile(path, raw, 0600)
	if err := protectUnattendedConfig(path); err != nil {
		t.Fatal(err)
	}
	err := applyUnattendedEnrollment(unattendedOptions{URL: "https://example.com", TokenFile: path, Apps: []string{"cursor"}}, home, "synthetic", "machine-failed", &bytes.Buffer{})
	if err == nil || !strings.Contains(err.Error(), "workstation identity") {
		t.Fatalf("got %v", err)
	}
}

func TestUnattendedWindowsSessionResolution(t *testing.T) {
	for _, test := range []struct {
		sessions      []uint32
		id            uint32
		pending, fail bool
	}{
		{pending: true}, {sessions: []uint32{7}, id: 7}, {sessions: []uint32{7, 9}, fail: true}, {sessions: []uint32{0}, fail: true},
	} {
		id, pending, err := selectUnattendedWindowsSession(test.sessions)
		if id != test.id || pending != test.pending || (err != nil) != test.fail {
			t.Fatalf("sessions=%v got %d pending=%v err=%v", test.sessions, id, pending, err)
		}
	}
}

func TestUnattendedAuthorityReaderDoesNotForwardUnknownFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "authority.json")
	raw := fmt.Sprintf(`{"schema_version":1,"token":"enr_synthetic","salt":"%s","expires_at":"%s","private_extra":"synthetic-private-field"}`, strings.Repeat("s", 64), time.Now().Add(time.Hour).UTC().Format(time.RFC3339))
	if err := os.WriteFile(path, []byte(raw), 0600); err != nil {
		t.Fatal(err)
	}
	if err := protectUnattendedConfig(path); err != nil {
		t.Fatal(err)
	}
	sanitized, err := readEnrollmentAuthority(path)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(sanitized, []byte("private_extra")) || bytes.Contains(sanitized, []byte("synthetic-private-field")) {
		t.Fatal("forwarded non-envelope fields across user boundary")
	}
}

func TestUnattendedClaudeCodeDiscoveryAndIdentityUseNativeConfig(t *testing.T) {
	home := testenv.SetTempHome(t)
	agent, _ := unattendedAgent("claude_code", home)
	state := &localEnrollmentState{AgentName: agent.Name, ConfigPath: agent.ConfigPath, Unattended: true, RuntimePrincipalID: strings.Repeat("d", 64), EnrollmentID: "synthetic-enrollment"}
	if err := saveLocalEnrollmentState(state); err != nil {
		t.Fatal(err)
	}
	var spec agentSpec
	for _, entry := range agentSpecs {
		if entry.Name == "Claude Code" {
			spec = entry
			break
		}
	}
	paths := configPathsForAgentSpec(home, spec)
	if len(paths) == 0 || paths[0] != agent.ConfigPath {
		t.Fatalf("native enrollment hidden by discovery: %v", paths)
	}
	normalized := normalizeDiscoveredAgent(agent)
	matching := managedAgentSummary{ID: "synthetic-agent", SessionSourceType: "claude_code", SessionSourceID: state.RuntimePrincipalID}
	if found := findManagedAgentForDiscovered([]managedAgentSummary{matching}, normalized); found == nil {
		t.Fatal("cannot find unattended identity for status/offboard")
	}
}

func TestUnattendedDoesNotResurrectOffboardedOrReplacedEnrollment(t *testing.T) {
	for _, mode := range []string{"offboarded", "restored", "missing_state", "replaced"} {
		t.Run(mode, func(t *testing.T) {
			home := testenv.SetTempHome(t)
			keyring.MockInit()
			agent, _ := unattendedAgent("cursor", home)
			state := &localEnrollmentState{AgentName: agent.Name, ConfigPath: agent.ConfigPath, Unattended: true, EnrollmentID: "synthetic-original"}
			switch mode {
			case "offboarded":
				state.OffboardArchived = true
			case "restored":
				now := time.Now()
				state.RestoredAt = &now
			case "replaced":
				state.EnrollmentID = "synthetic-replacement"
			}
			if mode != "missing_state" {
				if err := saveLocalEnrollmentState(state); err != nil {
					t.Fatal(err)
				}
			}
			checkpoint := unattendedCheckpoint{SchemaVersion: 1, Apps: []unattendedAppCredential{{Applied: true, App: "cursor", AgentID: "synthetic-agent", RuntimePrincipalID: strings.Repeat("a", 64), EnrollmentID: "synthetic-original", Token: "agt_synthetic", ExpiresAt: api.Time{Time: time.Now().Add(time.Hour)}}}}
			raw, _ := json.Marshal(checkpoint)
			_ = keyring.Set(enrollmentKeyringService, "synthetic-terminal", string(raw))
			err := applyUnattendedEnrollment(unattendedOptions{URL: "https://example.com", TokenFile: "unused", Apps: []string{"cursor"}}, home, "synthetic-user", "synthetic-terminal", &bytes.Buffer{})
			if err == nil {
				t.Fatal("resurrected terminal or replaced enrollment")
			}
			if _, err := os.Stat(agent.ConfigPath); !os.IsNotExist(err) {
				t.Fatal("changed config after terminal enrollment")
			}
		})
	}
}
