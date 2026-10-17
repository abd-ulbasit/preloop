package cmd

import (
	"github.com/preloop/preloop/cli/internal/api"
	"strings"
	"sync/atomic"
	"time"
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
	if err != nil || strings.TrimSpace(machineID) == "" {
		return ""
	}
	return workstationFingerprint(salt, machineID)
}

// Cached identity is read by the heartbeat without doing network I/O. Refresh
// the account salt in the background so a rotation reaches the next heartbeat.
var runnerFingerprint atomic.Value

func addRunnerFingerprint(msg map[string]any) {
	if runnerOnce.registersEphemeral() {
		return
	}
	if fingerprint, ok := runnerFingerprint.Load().(string); ok && fingerprint != "" {
		msg["workstation_fingerprint"] = fingerprint
	}
}

func refreshRunnerFingerprint(client *api.Client, deviceRef string, stop <-chan struct{}) {
	fingerprint := runnerWorkstationFingerprint(client, deviceRef)
	select {
	case <-stop:
		return
	default:
	}
	if fingerprint != "" {
		runnerFingerprint.Store(fingerprint)
	}
}

func startRunnerFingerprintRefresh(client *api.Client, deviceRef string) func() {
	if runnerOnce.registersEphemeral() {
		return func() {}
	}
	stop := make(chan struct{})
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ticker.C:
				refreshRunnerFingerprint(client, deviceRef, stop)
			}
		}
	}()
	return func() { close(stop) }
}
