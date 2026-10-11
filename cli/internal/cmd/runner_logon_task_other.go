//go:build !windows

package cmd

import "strings"

// decodeWindowsConsoleBytes has no OEM code page to consult off Windows;
// bytes that are not UTF-8 are dropped.
var decodeWindowsConsoleBytes = func(raw []byte) string {
	return strings.ToValidUTF8(string(raw), "")
}

// windowsTaskCreationPolicy has nothing to read off Windows; Checked stays
// false so the error text never claims a policy was inspected.
var windowsTaskCreationPolicy = func() windowsTaskPolicy {
	return windowsTaskPolicy{}
}
