package cmd

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
)

func TestRunnerFingerprintMatchesDiscovery(t *testing.T) {
	setupDiscoveryHome(t)
	original := readMachineID
	readMachineID = func() (string, error) { return testMachineID, nil }
	t.Cleanup(func() { readMachineID = original })
	server, _ := newDiscoveryServer(t)
	defer server.Close()
	client, err := api.NewClient("tok", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	got := runnerWorkstationFingerprint(client)
	if got != workstationFingerprint(testDiscoverySalt, testMachineID) {
		t.Fatalf("fingerprint mismatch: %q", got)
	}
	if got == workstationFingerprint("other-tenant-salt", testMachineID) {
		t.Fatal("cross-account identity")
	}
}

func TestRunnerFingerprintOlderServerOmitsIdentity(t *testing.T) {
	server := httptest.NewServer(http.NotFoundHandler())
	defer server.Close()
	client, err := api.NewClient("tok", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	if got := runnerWorkstationFingerprint(client); got != "" {
		t.Fatalf("got %q", got)
	}
}

func TestRunnerFingerprintExplicitReferenceMatchesDiscovery(t *testing.T) {
	original := readMachineID
	readMachineID = func() (string, error) { t.Fatal("must not read machine id with explicit reference"); return "", nil }
	t.Cleanup(func() { readMachineID = original })
	server, _ := newDiscoveryServer(t)
	client, err := api.NewClient("tok", server.URL)
	if err != nil {
		t.Fatal(err)
	}
	if got := runnerWorkstationFingerprint(client, "example-device"); got != workstationFingerprint(testDiscoverySalt, "example-device") {
		t.Fatalf("fingerprint mismatch: %q", got)
	}
}
