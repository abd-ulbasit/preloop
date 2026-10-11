package cmd

import (
	"bytes"
	"encoding/csv"
	"encoding/xml"
	"fmt"
	"io"
	"regexp"
	"strings"
	"unicode/utf16"
	"unicode/utf8"
)

// The Windows logon task that runs the personal runner (#1513).
//
// The task is registered from an XML definition rather than `schtasks /SC
// ONLOGON`: a logon trigger scoped to one user plus an InteractiveToken,
// LeastPrivilege principal is what a standard user may register without
// elevation (an unscoped "any user" logon trigger needs administrator
// rights), and the definition also drops the schtasks defaults that are
// wrong for a long-lived runner (a 72 hour execution limit, no start and a
// stop on battery power). The task runs in the user's interactive session,
// so the harness logins in that profile stay visible, it keeps running
// while the screen is locked, and it ends with the session at sign-out.
//
// Everything here is pure (no schtasks, no registry) so it is unit-tested
// on every OS. runner.go wires it to schtasks; the registry policy probe
// lives in runner_logon_task_windows.go with a stub for other platforms.

const (
	// windowsLogonTaskPrefix starts every per-user task name. The 0.17.0
	// CLI registered exactly this name for every user, which is why it is
	// also the legacy name enable, disable and status still look for.
	windowsLogonTaskPrefix     = "PreloopRunner"
	windowsLegacyLogonTaskName = windowsLogonTaskPrefix
	windowsLogonTaskSuffixMax  = 64
	windowsTaskNoTimeLimit     = "PT0S"
)

// windowsTaskNameUnsafeRe matches what a task name must not contain (path
// separators and the characters the Task Scheduler rejects) plus
// whitespace, which schtasks quoting handles but a name is clearer without.
var windowsTaskNameUnsafeRe = regexp.MustCompile(`[^A-Za-z0-9._-]+`)

// windowsLogonTaskName is the per-user task name. Task names are global in
// the Task Scheduler root (one file per name under System32\Tasks), so a
// second user on the same machine needs a second name; the user part of
// DOMAIN\user is the suffix.
func windowsLogonTaskName(username string) string {
	user := username
	if i := strings.LastIndex(user, `\`); i >= 0 {
		user = user[i+1:]
	}
	user = windowsTaskNameUnsafeRe.ReplaceAllString(strings.TrimSpace(user), "_")
	user = strings.Trim(user, "_")
	if len(user) > windowsLogonTaskSuffixMax {
		user = user[:windowsLogonTaskSuffixMax]
	}
	if user == "" {
		return windowsLogonTaskPrefix
	}
	return windowsLogonTaskPrefix + "-" + user
}

// windowsLogonTaskUser derives the task principal (DOMAIN\user, as the Task
// Scheduler GUI writes it) and the task name from os/user's Username and
// the machine name. A bare local name is qualified with the machine name so
// a local account resolves unambiguously.
func windowsLogonTaskUser(username, machine string) (string, string) {
	username = strings.TrimSpace(username)
	userID := username
	if !strings.ContainsAny(username, `\`) && username != "" && strings.TrimSpace(machine) != "" {
		userID = strings.TrimSpace(machine) + `\` + username
	}
	return userID, windowsLogonTaskName(username)
}

// windowsLogonTaskSpec is what the task definition needs.
type windowsLogonTaskSpec struct {
	Name       string
	UserID     string
	PowerShell string
	ScriptPath string
	WorkingDir string
}

// windowsLogonTaskXML renders the Task Scheduler definition. Element order
// follows what Windows exports, so the schema validator accepts it on every
// supported version.
func windowsLogonTaskXML(spec windowsLogonTaskSpec) string {
	arguments := fmt.Sprintf(
		`-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%s"`, spec.ScriptPath,
	)
	description := fmt.Sprintf(
		"Preloop runner for %s: runs preloop runner fg in this user's interactive session. "+
			"Registered without elevation by preloop runner enable; remove with preloop runner disable.",
		spec.UserID,
	)
	return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Author>` + xmlEscape(spec.UserID) + `</Author>
    <Description>` + xmlEscape(description) + `</Description>
    <URI>\` + xmlEscape(spec.Name) + `</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>` + xmlEscape(spec.UserID) + `</UserId>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>` + xmlEscape(spec.UserID) + `</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>` + windowsTaskNoTimeLimit + `</ExecutionTimeLimit>
    <Priority>5</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>` + xmlEscape(spec.PowerShell) + `</Command>
      <Arguments>` + xmlEscape(arguments) + `</Arguments>
      <WorkingDirectory>` + xmlEscape(spec.WorkingDir) + `</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
`
}

// encodeUTF16LEWithBOM is the file encoding schtasks /XML expects for a
// definition that declares encoding="UTF-16" (what Windows itself exports).
func encodeUTF16LEWithBOM(s string) []byte {
	units := utf16.Encode([]rune(s))
	out := make([]byte, 0, 2+2*len(units))
	out = append(out, 0xFF, 0xFE)
	for _, unit := range units {
		out = append(out, byte(unit), byte(unit>>8))
	}
	return out
}

// decodeWindowsTaskOutput turns schtasks output into UTF-8 text whatever
// encoding it used: UTF-16LE with a BOM (what /XML writes), UTF-8 with a
// BOM, or plain text.
func decodeWindowsTaskOutput(raw []byte) string {
	switch {
	case len(raw) >= 2 && raw[0] == 0xFF && raw[1] == 0xFE:
		units := make([]uint16, 0, (len(raw)-2)/2)
		for i := 2; i+1 < len(raw); i += 2 {
			units = append(units, uint16(raw[i])|uint16(raw[i+1])<<8)
		}
		return string(utf16.Decode(units))
	case bytes.HasPrefix(raw, []byte{0xEF, 0xBB, 0xBF}):
		return string(raw[3:])
	case utf8.Valid(raw):
		return string(raw)
	default:
		return strings.ToValidUTF8(string(raw), "")
	}
}

// windowsTaskDefinition is the part of a task definition status reports:
// who the task runs as and how, and the two settings that tell a 0.17.0
// task from a current one.
type windowsTaskDefinition struct {
	UserID              string
	LogonType           string
	RunLevel            string
	ExecutionTimeLimit  string
	TriggerScopedToUser bool
}

type windowsTaskXML struct {
	Triggers struct {
		LogonTrigger []struct {
			UserID string `xml:"UserId"`
		} `xml:"LogonTrigger"`
	} `xml:"Triggers"`
	Principals struct {
		Principal []struct {
			UserID    string `xml:"UserId"`
			LogonType string `xml:"LogonType"`
			RunLevel  string `xml:"RunLevel"`
		} `xml:"Principal"`
	} `xml:"Principals"`
	Settings struct {
		ExecutionTimeLimit string `xml:"ExecutionTimeLimit"`
	} `xml:"Settings"`
}

// parseWindowsTaskXML reads `schtasks /Query /XML ONE` output.
func parseWindowsTaskXML(raw []byte) (windowsTaskDefinition, error) {
	text := decodeWindowsTaskOutput(raw)
	decoder := xml.NewDecoder(strings.NewReader(text))
	// The declaration says UTF-16 but the text is UTF-8 by now.
	decoder.CharsetReader = func(string, io.Reader) (io.Reader, error) {
		return strings.NewReader(text[strings.Index(text, "?>")+2:]), nil
	}
	var doc windowsTaskXML
	if err := decoder.Decode(&doc); err != nil {
		return windowsTaskDefinition{}, fmt.Errorf("task definition: %w", err)
	}
	def := windowsTaskDefinition{
		ExecutionTimeLimit: strings.TrimSpace(doc.Settings.ExecutionTimeLimit),
	}
	if len(doc.Principals.Principal) > 0 {
		principal := doc.Principals.Principal[0]
		def.UserID = strings.TrimSpace(principal.UserID)
		def.LogonType = strings.TrimSpace(principal.LogonType)
		def.RunLevel = strings.TrimSpace(principal.RunLevel)
	}
	for _, trigger := range doc.Triggers.LogonTrigger {
		if strings.TrimSpace(trigger.UserID) != "" {
			def.TriggerScopedToUser = true
		}
	}
	return def, nil
}

// windowsRunnerContextLines is what `preloop runner status` prints under
// install: on Windows. It describes the task as registered, not as this
// CLI would register it, so a hand-edited or legacy task is reported as
// it is.
func windowsRunnerContextLines(taskName string, def windowsTaskDefinition) []string {
	user := def.UserID
	if user == "" {
		user = "the task owner"
	}
	var context string
	if def.LogonType == "InteractiveToken" && (def.RunLevel == "" || def.RunLevel == "LeastPrivilege") {
		context = fmt.Sprintf(
			"context: logon task %s runs as %s in the interactive session, least privilege (no admin rights)",
			taskName, user,
		)
	} else {
		context = fmt.Sprintf(
			"context: logon task %s runs as %s with logon type %s, run level %s",
			taskName, user, orUnknown(def.LogonType), orUnknown(def.RunLevel),
		)
	}
	lines := []string{
		context,
		"lock: keeps running while the screen is locked",
		"sign-out: ends with the session; the runner reports offline until the next logon",
		"other users: each Windows user gets a separate task and runner identity",
	}
	if def.ExecutionTimeLimit != windowsTaskNoTimeLimit || !def.TriggerScopedToUser {
		limit := def.ExecutionTimeLimit
		if limit == "" {
			limit = "the default limit"
		}
		lines = append(lines, fmt.Sprintf(
			"upgrade: the task stops the runner after %s and starts for any user; re-run preloop runner enable",
			limit,
		))
	}
	return lines
}

func orUnknown(value string) string {
	if strings.TrimSpace(value) == "" {
		return "unknown"
	}
	return value
}

// windowsTaskStatusFromCSV extracts the Status column from
// `schtasks /Query /TN <name> /FO CSV` (with or without the header row).
// The word is whatever the OS language prints (Running, Ready, Disabled).
func windowsTaskStatusFromCSV(raw string) string {
	reader := csv.NewReader(strings.NewReader(raw))
	reader.FieldsPerRecord = -1
	reader.LazyQuotes = true
	rows, err := reader.ReadAll()
	if err != nil {
		return ""
	}
	for _, row := range rows {
		if len(row) < 3 || !strings.HasPrefix(strings.TrimSpace(row[0]), `\`) {
			continue
		}
		return strings.TrimSpace(row[2])
	}
	return ""
}

// windowsTaskPolicy is what the registry says about task creation. Checked
// is false when the probe could not run (non-Windows, registry error).
type windowsTaskPolicy struct {
	Checked            bool
	CreationProhibited bool
}

const (
	windowsTaskPolicyName = `Group Policy "Prohibit New Task Creation"`
	windowsTaskPolicyKey  = `HKLM\SOFTWARE\Policies\Microsoft\Windows\Task Scheduler5.0\Task Creation`
)

var windowsTaskAccessDeniedRe = regexp.MustCompile(`(?i)access is denied|0x80070005|E_ACCESSDENIED`)

var windowsTaskSchedulerDownRe = regexp.MustCompile(
	`(?i)Task Scheduler service is not running|RPC server is unavailable|0x800706BA|0x80041315`,
)

// windowsLogonTaskCreateError turns a failed `schtasks /Create /XML` into an
// error that names the cause and what to do: the policy when the registry
// says task creation is prohibited, the Task Scheduler service when it is
// down, and in every case the two ways out (an administrator registering
// the written XML for this user, or running the runner in a terminal).
func windowsLogonTaskCreateError(
	taskName, xmlPath, output string, exitErr error, policy windowsTaskPolicy,
) error {
	output = strings.TrimSpace(output)
	adminHint := fmt.Sprintf(
		`an administrator can register it for you with: schtasks /Create /TN "%s" /XML "%s" /F (the task still runs as you, with no admin rights)`,
		taskName, xmlPath,
	)
	terminalHint := "without a task, start the runner from a terminal with: preloop runner fg"
	switch {
	case policy.CreationProhibited:
		return fmt.Errorf(
			"task_creation_prohibited_by_policy: Windows refused to register the logon task %s (%s). %s prohibits scheduled task creation on this machine (%s = 1). Ask your administrator to exempt this device; %s: %w",
			taskName, output, windowsTaskPolicyName, windowsTaskPolicyKey, terminalHint, exitErr,
		)
	case windowsTaskAccessDeniedRe.MatchString(output):
		return fmt.Errorf(
			"task_creation_denied: Windows refused to register the logon task %s (%s). The task needs no elevation, so a device policy or a hardened Task Scheduler folder blocks task creation for your account; %s, or %s: %w",
			taskName, output, adminHint, terminalHint, exitErr,
		)
	case windowsTaskSchedulerDownRe.MatchString(output):
		return fmt.Errorf(
			"task_scheduler_unavailable: the Task Scheduler service (Schedule) is not running or is disabled, so the logon task %s could not be registered (%s). Ask your administrator to enable it; %s: %w",
			taskName, output, terminalHint, exitErr,
		)
	default:
		return fmt.Errorf(
			"task_creation_failed: schtasks could not register the logon task %s (%s); %s, or %s: %w",
			taskName, output, adminHint, terminalHint, exitErr,
		)
	}
}
