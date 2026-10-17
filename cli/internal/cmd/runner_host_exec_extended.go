package cmd

import "strings"

func isExtendedHostExecHarness(h string) bool {
	return h == hostExecHarnessCodex || h == hostExecHarnessOpenCode || h == hostExecHarnessDeepSeek
}

// Runner-owned overrides replace, rather than duplicate, inherited names.
func mergeHostExecManagedEnv(base, extra []string) []string {
	keys := map[string]bool{}
	for _, entry := range extra {
		keys[strings.ToUpper(strings.SplitN(entry, "=", 2)[0])] = true
	}
	out := make([]string, 0, len(base)+len(extra))
	for _, entry := range base {
		if !keys[strings.ToUpper(strings.SplitN(entry, "=", 2)[0])] {
			out = append(out, entry)
		}
	}
	return append(out, extra...)
}
