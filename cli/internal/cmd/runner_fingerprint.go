package cmd

import (
	"github.com/preloop/preloop/cli/internal/api"
	"strings"
)

// runnerWorkstationFingerprint uses exactly the discovery account salt and
// namespace. Older servers or unavailable machine identity leave it unreported;
// registration remains usable and the device projection keeps the runner separate.
func runnerWorkstationFingerprint(client *api.Client, deviceRefs ...string) string {
	salt, err := fetchDiscoverySalt(client)
	if err != nil {
		return ""
	}
	machineID := ""
	if len(deviceRefs) > 0 {
		machineID = strings.TrimSpace(deviceRefs[0])
	}
	if machineID == "" {
		machineID, err = readMachineID()
	}
	if err != nil || machineID == "" {
		return ""
	}
	return workstationFingerprint(salt, machineID)
}
