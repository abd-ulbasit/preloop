package cmd

import (
	"bufio"
	"fmt"
	"io"

	"github.com/preloop/preloop/cli/internal/config"
	"github.com/spf13/cobra"
)

// runnerConsentOverride applies an explicit foreground choice on re-registration.
var runnerConsentOverride string

func prepareRunnerConsent(cmd *cobra.Command) error {
	choice, _ := cmd.Flags().GetString("flow-execution-consent")
	runnerConsentOverride = choice
	value, managed, err := config.RunnerFlowExecutionConsent()
	if err != nil {
		return err
	}
	if managed {
		if choice != "" && choice != value {
			return fmt.Errorf("flow execution consent is controlled by managed configuration")
		}
		return nil
	}
	if choice != "" {
		return config.SetRunnerFlowExecutionConsent(choice)
	}
	if value != "" {
		return nil
	}
	if loginTerminal() {
		return chooseRunnerConsent(cmd.InOrStdin(), cmd.OutOrStdout(), true, "")
	}
	// Foreground runners can start safely with the default owner approval.
	// Setup installs require an explicit unattended choice.
	return nil
}

func chooseRunnerConsent(in io.Reader, out io.Writer, tty bool, choice string) error {
	value, managed, err := config.RunnerFlowExecutionConsent()
	if err != nil {
		return err
	}
	if managed {
		if choice != "" && choice != value {
			return fmt.Errorf("flow execution consent is controlled by managed configuration")
		}
		fmt.Fprintf(out, "Flow execution permission is managed by your organisation: %s.\n", value)
		return nil
	}
	if choice != "" {
		return config.SetRunnerFlowExecutionConsent(choice)
	}
	if !tty {
		return fmt.Errorf("unattended runner setup requires --flow-execution-consent=always|ask_me|never or managed configuration")
	}
	if value == "" {
		value = "ask_me"
	}
	reader, ok := in.(*bufio.Reader)
	if !ok {
		reader = bufio.NewReader(in)
	}
	fmt.Fprintln(out, "May Preloop execute flows on this runner?")
	fmt.Fprintln(out, "  ask_me: ask for your approval before each flow")
	fmt.Fprintln(out, "  always: allow flows automatically")
	fmt.Fprintln(out, "  never: do not allow flows")
	answer, err := promptForTextInput(reader, out, fmt.Sprintf("Flow execution permission [always/ask_me/never] (default: %s): ", value))
	if err != nil {
		return err
	}
	if answer == "" {
		answer = value
	}
	return config.SetRunnerFlowExecutionConsent(answer)
}
