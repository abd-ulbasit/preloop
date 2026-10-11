package cmd

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
)

func TestModelsCodexWSSendsOverride(t *testing.T) {
	cases := []struct {
		mode string
		want interface{}
		meta string
		text string
	}{
		{"on", true, `{"codex_upstream_websocket":true}`, "on (model override)"},
		{"off", false, `{"codex_upstream_websocket":false}`, "off (model override)"},
		{"inherit", nil, `{}`, "inherits"},
	}
	for _, tc := range cases {
		t.Run(tc.mode, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPut || r.URL.Path != "/api/v1/ai-models/m-1" {
					t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
				}
				var body map[string]interface{}
				_ = json.NewDecoder(r.Body).Decode(&body)
				got, present := body["codex_upstream_websocket"]
				if !present || got != tc.want {
					t.Errorf("body = %v, want %v", body, tc.want)
				}
				w.Header().Set("Content-Type", "application/json")
				w.Write([]byte(`{"id":"m-1","name":"Sol","meta_data":` + tc.meta + `}`))
			}))
			defer server.Close()
			client, err := api.NewClient("test-token", server.URL)
			if err != nil {
				t.Fatal(err)
			}
			var out bytes.Buffer
			if err := executeModelsCodexWS(client, &out, "m-1", tc.mode); err != nil {
				t.Fatal(err)
			}
			if !strings.Contains(out.String(), tc.text) {
				t.Errorf("output %q missing %q", out.String(), tc.text)
			}
		})
	}
}

func TestModelsCodexWSRejectsUnknownMode(t *testing.T) {
	if err := executeModelsCodexWS(nil, &bytes.Buffer{}, "m-1", "maybe"); err == nil {
		t.Fatal("expected error")
	}
}
