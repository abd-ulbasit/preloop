//go:build windows

package cmd

import (
	"strings"
	"testing"
	"unicode/utf16"
)

func TestUnattendedWindowsEnvironmentUsesTargetProfileAndDisablesTelemetry(t *testing.T) {
	block, err := enrollmentWindowsEnvironment([]string{`USERPROFILE=C:\Users\Jane`, "preloop_disable_telemetry=false", "ZETA=synthetic"})
	if err != nil {
		t.Fatal(err)
	}
	raw := string(utf16.Decode(block))
	if !strings.HasSuffix(raw, "\x00\x00") {
		t.Fatal("environment lacks double NUL terminator")
	}
	if !strings.Contains(raw, `USERPROFILE=C:\Users\Jane`) {
		t.Fatal("target profile dropped")
	}
	if strings.Contains(raw, "false") || strings.Count(strings.ToUpper(raw), "PRELOOP_DISABLE_TELEMETRY=") != 1 {
		t.Fatalf("unexpected environment %q", raw)
	}
	if !strings.Contains(raw, "PRELOOP_DISABLE_TELEMETRY=true") {
		t.Fatal("scripted target process would emit telemetry")
	}
}

func TestUnattendedWindowsAuthorityACLPrincipals(t *testing.T) {
	const target = "S-1-5-21-100-200-300-1001"
	for _, sid := range []string{target, "S-1-5-18", "S-1-5-32-544"} {
		if !enrollmentTrustedSID(sid, target) {
			t.Errorf("refused trusted %s", sid)
		}
	}
	for _, sid := range []string{"S-1-1-0", "S-1-5-11", "S-1-5-21-100-200-300-1002"} {
		if enrollmentTrustedSID(sid, target) {
			t.Errorf("trusted unrelated %s", sid)
		}
	}
}
