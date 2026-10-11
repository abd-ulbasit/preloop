package cmd

import (
	"errors"
	"strings"
	"testing"
	"unicode/utf16"
)

func TestWindowsLogonTaskNameIsPerUser(t *testing.T) {
	cases := []struct {
		username string
		want     string
	}{
		{`DESKTOP-1\jane`, "PreloopRunner-jane"},
		{"jane", "PreloopRunner-jane"},
		{`CORP\Jane Doe`, "PreloopRunner-Jane_Doe"},
		{`AzureAD\jane.doe@example.com`, "PreloopRunner-jane.doe_example.com"},
		// Characters the Task Scheduler refuses in a name are replaced.
		{`CORP\a/b:c*d?e"f<g>h|i`, "PreloopRunner-a_b_c_d_e_f_g_h_i"},
		// No user at all still yields a usable name, never a trailing dash.
		{"", "PreloopRunner"},
		{`CORP\`, "PreloopRunner"},
	}
	for _, tc := range cases {
		if got := windowsLogonTaskName(tc.username); got != tc.want {
			t.Errorf("windowsLogonTaskName(%q) = %q, want %q", tc.username, got, tc.want)
		}
	}
	if windowsLogonTaskName(`CORP\`+strings.Repeat("x", 300)) != "PreloopRunner-"+strings.Repeat("x", 64) {
		t.Error("the suffix is capped at 64 characters")
	}
}

func TestWindowsLogonTaskXMLRunsAsTheUserWithoutLimits(t *testing.T) {
	spec := windowsLogonTaskSpec{
		Name:       "PreloopRunner-jane",
		UserID:     `DESKTOP-1\jane`,
		PowerShell: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`,
		ScriptPath: `C:\Users\jane\.preloop\runner-task.ps1`,
		WorkingDir: `C:\Users\jane`,
	}
	xmlText := windowsLogonTaskXML(spec)
	for _, want := range []string{
		`<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">`,
		`<URI>\PreloopRunner-jane</URI>`,
		// The logon trigger is scoped to this user, which is what lets a
		// standard user register it: an unscoped (any user) logon trigger
		// needs administrator rights.
		"<LogonTrigger>\n      <Enabled>true</Enabled>\n      <UserId>DESKTOP-1\\jane</UserId>\n    </LogonTrigger>",
		`<Principal id="Author">`,
		`<UserId>DESKTOP-1\jane</UserId>`,
		"<LogonType>InteractiveToken</LogonType>",
		"<RunLevel>LeastPrivilege</RunLevel>",
		// No 72 hour kill, no battery conditions, keep running when idle
		// ends, one instance.
		"<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
		"<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
		"<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>",
		"<StopOnIdleEnd>false</StopOnIdleEnd>",
		"<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
		"<StartWhenAvailable>true</StartWhenAvailable>",
		"<RestartOnFailure>",
		`<Command>C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe</Command>`,
		`<Arguments>-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\jane\.preloop\runner-task.ps1"</Arguments>`,
		`<WorkingDirectory>C:\Users\jane</WorkingDirectory>`,
		`<Actions Context="Author">`,
	} {
		if !strings.Contains(xmlText, want) {
			t.Errorf("task XML missing %q in:\n%s", want, xmlText)
		}
	}
	if strings.Contains(xmlText, "HighestAvailable") || strings.Contains(xmlText, "S4U") || strings.Contains(xmlText, "Password") {
		t.Fatalf("task must stay least privilege, interactive token:\n%s", xmlText)
	}
}

func TestWindowsLogonTaskXMLEscapesValues(t *testing.T) {
	spec := windowsLogonTaskSpec{
		Name:       "PreloopRunner-o_hara",
		UserID:     `CORP\o'hara & <co>`,
		PowerShell: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`,
		ScriptPath: `C:\Users\o'hara & <co>\.preloop\runner-task.ps1`,
		WorkingDir: `C:\Users\o'hara & <co>`,
	}
	xmlText := windowsLogonTaskXML(spec)
	if !strings.Contains(xmlText, `<UserId>CORP\o'hara &amp; &lt;co&gt;</UserId>`) {
		t.Fatalf("user id not escaped:\n%s", xmlText)
	}
	if !strings.Contains(xmlText, `-File "C:\Users\o'hara &amp; &lt;co&gt;\.preloop\runner-task.ps1"</Arguments>`) {
		t.Fatalf("arguments not escaped:\n%s", xmlText)
	}
	if strings.Contains(xmlText, "<co>") {
		t.Fatalf("raw angle brackets leaked:\n%s", xmlText)
	}
}

func TestEncodeUTF16LEWithBOM(t *testing.T) {
	got := encodeUTF16LEWithBOM("<a>é</a>")
	if got[0] != 0xFF || got[1] != 0xFE {
		t.Fatalf("missing BOM: % x", got[:2])
	}
	units := make([]uint16, 0, (len(got)-2)/2)
	for i := 2; i+1 < len(got); i += 2 {
		units = append(units, uint16(got[i])|uint16(got[i+1])<<8)
	}
	if back := string(utf16.Decode(units)); back != "<a>é</a>" {
		t.Fatalf("round trip = %q", back)
	}
}

func TestDecodeWindowsTaskOutput(t *testing.T) {
	utf8Text := `<?xml version="1.0" encoding="UTF-16"?><Task/>`
	if got := decodeWindowsTaskOutput(encodeUTF16LEWithBOM(utf8Text)); got != utf8Text {
		t.Fatalf("UTF-16 input = %q", got)
	}
	if got := decodeWindowsTaskOutput([]byte(utf8Text)); got != utf8Text {
		t.Fatalf("UTF-8 input = %q", got)
	}
	if got := decodeWindowsTaskOutput([]byte("\xEF\xBB\xBF" + utf8Text)); got != utf8Text {
		t.Fatalf("UTF-8 BOM input = %q", got)
	}
	// Bytes that are not UTF-8 (schtasks /FO CSV in the console OEM code
	// page) go through the platform decoder, whose Windows version uses
	// MultiByteToWideChar; here a stand-in maps code page 850's ü.
	previous := decodeWindowsConsoleBytes
	decodeWindowsConsoleBytes = func(raw []byte) string {
		return strings.ReplaceAll(string(raw), "\x81", "ü")
	}
	t.Cleanup(func() { decodeWindowsConsoleBytes = previous })
	oem := []byte("\"\\PreloopRunner-jane\",\"N/A\",\"Wird ausgef\x81hrt\"\r\n")
	if got := windowsTaskStatusFromCSV(decodeWindowsTaskOutput(oem)); got != "Wird ausgeführt" {
		t.Fatalf("OEM status = %q", got)
	}
	decodeWindowsConsoleBytes = previous
	if got := windowsTaskStatusFromCSV(decodeWindowsTaskOutput(oem)); got != "Wird ausgefhrt" {
		t.Fatalf("without a code page decoder the invalid byte is dropped, got %q", got)
	}
}

func TestParseWindowsTaskXMLReadsPrincipalAndLimit(t *testing.T) {
	spec := windowsLogonTaskSpec{
		Name:       "PreloopRunner-jane",
		UserID:     `DESKTOP-1\jane`,
		PowerShell: `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`,
		ScriptPath: `C:\Users\jane\.preloop\runner-task.ps1`,
		WorkingDir: `C:\Users\jane`,
	}
	// schtasks /Query /XML writes what it was given, with the UTF-16
	// declaration intact; the parser must accept that and the raw text.
	for _, raw := range [][]byte{
		encodeUTF16LEWithBOM(windowsLogonTaskXML(spec)),
		[]byte(windowsLogonTaskXML(spec)),
	} {
		def, err := parseWindowsTaskXML(raw)
		if err != nil {
			t.Fatal(err)
		}
		if def.UserID != `DESKTOP-1\jane` || def.LogonType != "InteractiveToken" || def.RunLevel != "LeastPrivilege" {
			t.Fatalf("principal = %+v", def)
		}
		if def.ExecutionTimeLimit != "PT0S" || !def.TriggerScopedToUser {
			t.Fatalf("settings = %+v", def)
		}
	}
}

func TestParseWindowsTaskXMLLegacyTask(t *testing.T) {
	// What `schtasks /Create /SC ONLOGON /RL LIMITED` (0.17.0) registered:
	// an any-user logon trigger and the 72 hour default limit.
	legacy := `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers><LogonTrigger><Enabled>true</Enabled></LogonTrigger></Triggers>
  <Principals><Principal id="Author"><UserId>DESKTOP-1\jane</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings><ExecutionTimeLimit>PT72H</ExecutionTimeLimit></Settings>
  <Actions Context="Author"><Exec><Command>powershell.exe</Command></Exec></Actions>
</Task>`
	def, err := parseWindowsTaskXML([]byte(legacy))
	if err != nil {
		t.Fatal(err)
	}
	if def.TriggerScopedToUser || def.ExecutionTimeLimit != "PT72H" {
		t.Fatalf("legacy = %+v", def)
	}
	if _, err := parseWindowsTaskXML([]byte("not xml")); err == nil {
		t.Fatal("garbage must not parse")
	}
}

func TestWindowsRunnerContextLines(t *testing.T) {
	current := windowsTaskDefinition{
		UserID: `DESKTOP-1\jane`, LogonType: "InteractiveToken", RunLevel: "LeastPrivilege",
		ExecutionTimeLimit: "PT0S", TriggerScopedToUser: true,
	}
	lines := windowsRunnerContextLines("PreloopRunner-jane", current)
	joined := strings.Join(lines, "\n")
	for _, want := range []string{
		`context: logon task PreloopRunner-jane runs as DESKTOP-1\jane in the interactive session, least privilege (no admin rights)`,
		"lock: keeps running while the screen is locked",
		"sign-out: ends with the session; the runner reports offline until the next logon",
		"other users: each Windows user gets a separate task and runner identity",
	} {
		if !strings.Contains(joined, want) {
			t.Errorf("missing %q in:\n%s", want, joined)
		}
	}
	if strings.Contains(joined, "upgrade") {
		t.Fatalf("a current task needs no upgrade note:\n%s", joined)
	}

	legacy := windowsTaskDefinition{
		UserID: `DESKTOP-1\jane`, LogonType: "InteractiveToken", RunLevel: "LeastPrivilege",
		ExecutionTimeLimit: "PT72H",
	}
	joined = strings.Join(windowsRunnerContextLines("PreloopRunner", legacy), "\n")
	if !strings.Contains(joined, "upgrade: the task stops the runner after PT72H and starts for any user; re-run preloop runner enable") {
		t.Fatalf("legacy task must say how to upgrade:\n%s", joined)
	}

	elevated := windowsTaskDefinition{
		UserID: `DESKTOP-1\jane`, LogonType: "S4U", RunLevel: "HighestAvailable",
		ExecutionTimeLimit: "PT0S", TriggerScopedToUser: true,
	}
	joined = strings.Join(windowsRunnerContextLines("PreloopRunner-jane", elevated), "\n")
	if !strings.Contains(joined, "runs as DESKTOP-1\\jane with logon type S4U, run level HighestAvailable") {
		t.Fatalf("a hand-edited task must be described as it is:\n%s", joined)
	}
	if strings.Contains(joined, "no admin rights") {
		t.Fatalf("must not claim least privilege for HighestAvailable:\n%s", joined)
	}
}

func TestWindowsLogonTaskCreateErrorNamesThePolicy(t *testing.T) {
	exit := errors.New("exit status 1")
	cases := []struct {
		name   string
		output string
		policy windowsTaskPolicy
		want   []string
		absent []string
	}{
		{
			name:   "prohibited by group policy",
			output: "ERROR: Access is denied.",
			policy: windowsTaskPolicy{Checked: true, CreationProhibited: true},
			want: []string{
				"task_creation_prohibited_by_policy",
				`Group Policy "Prohibit New Task Creation"`,
				`HKLM\SOFTWARE\Policies\Microsoft\Windows\Task Scheduler5.0\Task Creation`,
				"preloop runner fg",
				"PreloopRunner-jane",
			},
		},
		{
			name:   "access denied without a known policy",
			output: "ERROR: Access is denied.",
			policy: windowsTaskPolicy{Checked: true},
			want: []string{
				"task_creation_denied",
				"a device policy or a hardened",
				"an administrator can register it for you",
				`schtasks /Create /TN "PreloopRunner-jane" /XML "C:\Users\jane\.preloop\runner-task.xml" /F`,
				"preloop runner fg",
			},
			absent: []string{"Prohibit New Task Creation"},
		},
		{
			name:   "access denied in another locale still names the policy when the registry says so",
			output: "FEHLER: Zugriff verweigert",
			policy: windowsTaskPolicy{Checked: true, CreationProhibited: true},
			want:   []string{"task_creation_prohibited_by_policy", "Zugriff verweigert"},
		},
		{
			name:   "scheduler service not running",
			output: "ERROR: The Task Scheduler service is not running.",
			policy: windowsTaskPolicy{Checked: true},
			want:   []string{"task_scheduler_unavailable", "Task Scheduler service (Schedule)", "preloop runner fg"},
		},
		{
			name:   "unknown failure keeps the schtasks output",
			output: "ERROR: something else happened",
			policy: windowsTaskPolicy{},
			want:   []string{"task_creation_failed", "something else happened", "preloop runner fg"},
			absent: []string{"elevated prompt"},
		},
	}
	for _, tc := range cases {
		err := windowsLogonTaskCreateError(
			"PreloopRunner-jane", `C:\Users\jane\.preloop\runner-task.xml`, tc.output, exit, tc.policy,
		)
		if err == nil {
			t.Fatalf("%s: want an error", tc.name)
		}
		for _, want := range tc.want {
			if !strings.Contains(err.Error(), want) {
				t.Errorf("%s: missing %q in:\n%s", tc.name, want, err)
			}
		}
		for _, absent := range tc.absent {
			if strings.Contains(err.Error(), absent) {
				t.Errorf("%s: unexpected %q in:\n%s", tc.name, absent, err)
			}
		}
		if !errors.Is(err, exit) {
			t.Errorf("%s: must wrap the exec error", tc.name)
		}
	}
}

func TestWindowsTaskStatusFromCSV(t *testing.T) {
	cases := []struct {
		raw  string
		want string
	}{
		{"\"TaskName\",\"Next Run Time\",\"Status\"\r\n\"\\PreloopRunner-jane\",\"N/A\",\"Running\"\r\n", "Running"},
		{"\"\\PreloopRunner-jane\",\"N/A\",\"Ready\"\r\n", "Ready"},
		// Localised status words are printed as they are.
		{"\"\\PreloopRunner-jane\",\"N/A\",\"Wird ausgeführt\"\r\n", "Wird ausgeführt"},
		{"", ""},
		{"garbage", ""},
	}
	for _, tc := range cases {
		if got := windowsTaskStatusFromCSV(tc.raw); got != tc.want {
			t.Errorf("status(%q) = %q, want %q", tc.raw, got, tc.want)
		}
	}
}

func TestWindowsLogonTaskUserFromCurrent(t *testing.T) {
	cases := []struct {
		username, name string
		wantUserID     string
		wantName       string
	}{
		{`DESKTOP-1\jane`, "", `DESKTOP-1\jane`, "PreloopRunner-jane"},
		// A bare name (no domain part) is qualified with the machine name
		// so the Task Scheduler resolves a local account unambiguously.
		{"jane", "DESKTOP-1", `DESKTOP-1\jane`, "PreloopRunner-jane"},
		{"jane", "", "jane", "PreloopRunner-jane"},
	}
	for _, tc := range cases {
		userID, name := windowsLogonTaskUser(tc.username, tc.name)
		if userID != tc.wantUserID || name != tc.wantName {
			t.Errorf("windowsLogonTaskUser(%q, %q) = %q, %q", tc.username, tc.name, userID, name)
		}
	}
}
