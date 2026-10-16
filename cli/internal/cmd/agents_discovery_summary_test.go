package cmd

import (
	"bytes"
	"context"
	"fmt"
	"net/url"
	"strings"
	"testing"
)

func TestDiscoverySummaryAccountsForEveryAgent(t *testing.T) {
	agents := []AgentConfig{
		{Name: "Cursor", ConfigPath: "/cursor", IsOnboarded: true, OnboardingState: "mcp_proxy_only"},
		{Name: "Antigravity", ConfigPath: "/antigravity", IsOnboarded: true, OnboardingState: "mcp_proxy_only"},
		{Name: "Pi", ConfigPath: "/pi", RuntimeState: "missing"},
		{Name: "Claude Code", ConfigPath: "/claude"},
	}
	outcomes := completeDiscoveryOutcomes(agents, []agentOnboardingOutcome{{Agent: agents[3], Status: agentOnboardingStatusPartial, Reason: "probe timed out"}})
	if len(outcomes) != len(agents) {
		t.Fatalf("missing rows: %+v", outcomes)
	}
	for i, want := range []string{"unchanged", "unchanged", "skipped", "partial"} {
		if outcomes[i].Status != want {
			t.Fatalf("row %d: %+v", i, outcomes[i])
		}
	}
	for _, want := range []string{"Managed MCP bridge configured", "model routing: unsupported", "--approvals"} {
		if !strings.Contains(outcomes[0].Reason, want) {
			t.Errorf("missing %q: %s", want, outcomes[0].Reason)
		}
	}
	if !strings.Contains(outcomes[1].Reason, "native approvals unsupported") {
		t.Fatal(outcomes[1].Reason)
	}
}

func TestSkippedDiscoveryRowsDoNotHideFailures(t *testing.T) {
	if err := agentOnboardingSummaryError([]agentOnboardingOutcome{{Status: "skipped"}, {Status: "failed"}}); err == nil {
		t.Fatal("skipped row hid failure")
	}
	if err := agentOnboardingSummaryError([]agentOnboardingOutcome{{Status: "skipped"}}); err != nil {
		t.Fatal(err)
	}
}

func TestValidationTimeoutRetainsRoutingWithoutClaimingVerification(t *testing.T) {
	err := fmt.Errorf("request failed: %w", &url.Error{Op: "Post", URL: "https://example.com", Err: context.DeadlineExceeded})
	if !isLiveValidationTimeout(err) {
		t.Fatal("wrapped HTTP timeout not classified")
	}
	if isLiveValidationTimeout(fmt.Errorf("invalid credential")) {
		t.Fatal("auth error classified as timeout")
	}
	validation := map[string]interface{}{"live_validation_status": "timeout"}
	if !liveValidationStatusKeepsGatewayConfig(validation) {
		t.Fatal("timeout must retain routing")
	}
	if liveValidationStatusGatewayVerified(validation) {
		t.Fatal("timeout does not verify routing")
	}
	agent := AgentConfig{Name: "Claude Code"}
	outcomes := []agentOnboardingOutcome{{Agent: agent, Status: agentOnboardingStatusOnboarded}}
	applyLiveValidationOutcomesToSummary(outcomes, []deferredLiveValidationResult{{Agent: agent, Err: err, Outcome: &managedLiveValidationOutcome{Attempted: true, ValidationResult: validation}}})
	if outcomes[0].Status != agentOnboardingStatusPartial || !strings.Contains(outcomes[0].Reason, "inconclusive") {
		t.Fatal(outcomes)
	}
}

func TestWindsurfNotActivelyDiscovered(t *testing.T) {
	for _, spec := range agentSpecs {
		if spec.Name == "Windsurf" {
			t.Fatal("retired Windsurf spec remains")
		}
	}
}

func TestDeferredTimeoutDoesNotRollbackEitherGateway(t *testing.T) {
	for _, name := range []string{"Claude Code", "Codex CLI"} {
		t.Run(name, func(t *testing.T) {
			var output bytes.Buffer
			result := deferredLiveValidationResult{Agent: AgentConfig{Name: name}, Err: context.DeadlineExceeded, Outcome: &managedLiveValidationOutcome{Attempted: true, ValidationResult: map[string]interface{}{"live_validation_status": "timeout"}}}
			recoverDeferredGatewayValidationFailure(&output, result)
			if !strings.Contains(output.String(), "keeping the Preloop gateway configuration") || liveValidationGatewayRolledBack(result.Outcome.ValidationResult) {
				t.Fatal(output.String())
			}
			if deferredLiveValidationPersistStatus(result.Outcome) != "validation_inconclusive" {
				t.Fatal("timeout should persist as inconclusive")
			}
		})
	}
}

func TestRepairedConfigOverridesSavedRollbackBeforeValidation(t *testing.T) {
	saved := map[string]interface{}{"gateway_provider_ok": false, "gateway_base_url_ok": false, "gateway_token_ok": false, "live_validation_gateway_rolled_back": true, "live_validation_skip_reason": "old missing gateway"}
	current := map[string]interface{}{"preloop_server_present": true, "gateway_provider_ok": true, "gateway_base_url_ok": true, "gateway_token_ok": true}
	inputs := currentLiveValidationInputs(saved, current)
	if reason := missingManagedGatewayValidationPrerequisite(inputs); reason != "" {
		t.Fatal(reason)
	}
	if liveValidationGatewayRolledBack(inputs) {
		t.Fatal("stale rollback survived repaired config")
	}
	if _, ok := inputs["live_validation_skip_reason"]; ok {
		t.Fatal("stale skip reason survived new validation")
	}
	if saved["gateway_provider_ok"] != false {
		t.Fatal("saved result was mutated")
	}
}
