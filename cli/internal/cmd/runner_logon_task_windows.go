//go:build windows

package cmd

import (
	"errors"
	"strings"
	"unicode/utf16"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

// windowsCodePageOEM is CP_OEMCP: the console's default output code page,
// which schtasks uses for everything but /XML.
const windowsCodePageOEM = 1

// decodeWindowsConsoleBytes converts OEM code page bytes (schtasks /FO CSV
// and its error text) to UTF-8 through MultiByteToWideChar, so a localised
// status word keeps its accents. Undecodable input falls back to dropping
// the invalid bytes.
var decodeWindowsConsoleBytes = func(raw []byte) string {
	if len(raw) == 0 {
		return ""
	}
	fallback := strings.ToValidUTF8(string(raw), "")
	n, err := windows.MultiByteToWideChar(windowsCodePageOEM, 0, &raw[0], int32(len(raw)), nil, 0)
	if err != nil || n <= 0 {
		return fallback
	}
	buf := make([]uint16, n)
	if _, err := windows.MultiByteToWideChar(windowsCodePageOEM, 0, &raw[0], int32(len(raw)), &buf[0], n); err != nil {
		return fallback
	}
	return string(utf16.Decode(buf))
}

// windowsTaskCreationPolicy reads the Task Scheduler policy that prohibits
// new tasks (Group Policy "Prohibit New Task Creation", stored as the
// DWORD "Task Creation" under the Task Scheduler5.0 policy key). HKLM
// policy keys are readable by standard users; an absent key or value means
// no policy is set. The probe reads one integer and nothing else.
var windowsTaskCreationPolicy = func() windowsTaskPolicy {
	key, err := registry.OpenKey(
		registry.LOCAL_MACHINE,
		`SOFTWARE\Policies\Microsoft\Windows\Task Scheduler5.0`,
		registry.QUERY_VALUE,
	)
	if err != nil {
		if errors.Is(err, registry.ErrNotExist) {
			return windowsTaskPolicy{Checked: true}
		}
		return windowsTaskPolicy{}
	}
	defer key.Close()
	value, _, err := key.GetIntegerValue("Task Creation")
	if err != nil {
		if errors.Is(err, registry.ErrNotExist) {
			return windowsTaskPolicy{Checked: true}
		}
		return windowsTaskPolicy{}
	}
	return windowsTaskPolicy{Checked: true, CreationProhibited: value == 1}
}
