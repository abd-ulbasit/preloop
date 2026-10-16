package cmd

import "fmt"

// completeDiscoveryOutcomes retains discovery order and accounts for agents
// excluded from the onboarding prompt loop, without counting them as attempts.
func completeDiscoveryOutcomes(discovered []AgentConfig, attempted []agentOnboardingOutcome) []agentOnboardingOutcome {
	outcomes := make([]agentOnboardingOutcome, 0, len(discovered))
	for _, agent := range discovered {
		found := false
		for _, outcome := range attempted {
			if outcome.Agent.Name == agent.Name && outcome.Agent.ConfigPath == agent.ConfigPath {
				outcomes = append(outcomes, outcome)
				found = true
				break
			}
		}
		if found {
			continue
		}
		outcome := agentOnboardingOutcome{Agent: agent, Status: "skipped"}
		switch {
		case agentRuntimeMissing(agent):
			outcome.Reason = runtimeMissingListingLabel
			outcome.MissingExecutable = true
		case agent.IsOnboarded && !agent.ReonboardRecommended:
			outcome.Status = "unchanged"
			outcome.Reason = "existing enrollment; " + onboardingStateLabel(agent.OnboardingState)
			capabilities := capabilitiesForAgent(agent)
			outcome.Reason += "; model routing: " + controlSupportLabel(capabilities.ModelRoute)
			if isApprovalHookSupportedAgent(agent) {
				outcome.Reason += fmt.Sprintf("; native approvals supported (not checked); install/repair: preloop agents onboard %s --approvals", shellQuoteAgentName(resolveAgentDisplayName(agent)))
			} else {
				outcome.Reason += "; native approvals unsupported by current adapter"
			}
			outcome.Reason += "; application behavior unverified"
		default:
			outcome.Reason = "onboarding not attempted"
		}
		outcomes = append(outcomes, outcome)
	}
	return outcomes
}
