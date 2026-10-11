//go:build windows

package cmd

import (
	"strings"
	"testing"
	"unicode/utf8"

	"golang.org/x/sys/windows"
)

// TestWindowsTaskCreationPolicyProbeReadsTheRegistry runs the real probe:
// on a machine without the policy (the CI runner) it reports a completed
// check with creation allowed, and it never fails for a standard user.
func TestWindowsTaskCreationPolicyProbeReadsTheRegistry(t *testing.T) {
	policy := windowsTaskCreationPolicy()
	if !policy.Checked {
		t.Fatal("the policy key must be readable (absent key counts as checked)")
	}
	t.Logf("task creation prohibited by policy: %v", policy.CreationProhibited)
}

// TestDecodeWindowsConsoleBytesUsesTheOEMCodePage feeds the bytes a German
// console writes for "Wird ausgeführt" (0x81 is ü in code pages 437 and
// 850, the OEM pages of Western installs) through the real decoder and the
// CSV status parser.
func TestDecodeWindowsConsoleBytesUsesTheOEMCodePage(t *testing.T) {
	raw := []byte("\"\\PreloopRunner-jane\",\"N/A\",\"Wird ausgef\x81hrt\"\r\n")
	text := decodeWindowsTaskOutput(raw)
	if !utf8.ValidString(text) || !strings.Contains(text, "Wird ausgef") {
		t.Fatalf("decoded = %q", text)
	}
	status := windowsTaskStatusFromCSV(text)
	switch windows.GetACP() {
	case 1252:
		if status != "Wird ausgeführt" {
			t.Fatalf("status = %q, want the umlaut kept", status)
		}
	default:
		t.Logf("ANSI code page %d: status = %q", windows.GetACP(), status)
		if status == "" {
			t.Fatal("status lost")
		}
	}
}
