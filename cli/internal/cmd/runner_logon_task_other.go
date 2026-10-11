//go:build !windows

package cmd

// windowsTaskCreationPolicy has nothing to read off Windows; Checked stays
// false so the error text never claims a policy was inspected.
var windowsTaskCreationPolicy = func() windowsTaskPolicy {
	return windowsTaskPolicy{}
}
