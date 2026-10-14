package cmd

import (
	"encoding/json"
	"testing"
)

func TestBuildPermissionRequestCarriesFlowExecutionID(t *testing.T) {
	previous := permissionHookGetenv
	t.Cleanup(func() { permissionHookGetenv = previous })
	raw := []byte(`{"hook_event_name":"PreToolUse","session_id":"s-1","cwd":"/srv/repo","tool_name":"Bash","tool_input":{"command":"git status"}}`)

	permissionHookGetenv = func(key string) string {
		if key == "PRELOOP_FLOW_EXECUTION_ID" {
			return "8a1f2c3d-1111-4222-8333-944455556666"
		}
		return ""
	}
	req, err := buildPermissionRequest(permissionSourceClaudeCode, raw, permissionHookCredential{})
	if err != nil {
		t.Fatal(err)
	}
	if req.FlowExecutionID != "8a1f2c3d-1111-4222-8333-944455556666" || req.SessionID != "s-1" {
		t.Fatalf("request = %+v", req)
	}
	body, _ := json.Marshal(req)
	var decoded map[string]interface{}
	_ = json.Unmarshal(body, &decoded)
	if decoded["flow_execution_id"] != "8a1f2c3d-1111-4222-8333-944455556666" {
		t.Fatalf("body = %s", body)
	}

	for _, bad := range []string{"", "not-a-uuid", "8a1f2c3d-1111-4222-8333-944455556666; rm"} {
		value := bad
		permissionHookGetenv = func(key string) string {
			if key == "PRELOOP_FLOW_EXECUTION_ID" {
				return value
			}
			return ""
		}
		req, err := buildPermissionRequest(permissionSourceClaudeCode, raw, permissionHookCredential{})
		if err != nil {
			t.Fatal(err)
		}
		if req.FlowExecutionID != "" {
			t.Fatalf("accepted %q", bad)
		}
		body, _ := json.Marshal(req)
		var decoded map[string]interface{}
		_ = json.Unmarshal(body, &decoded)
		if _, present := decoded["flow_execution_id"]; present {
			t.Fatalf("flow_execution_id sent for %q: %s", bad, body)
		}
	}
}
