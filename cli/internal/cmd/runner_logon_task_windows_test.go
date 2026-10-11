//go:build windows

package cmd

import "testing"

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
