//go:build windows

package cmd

import (
	"errors"

	"golang.org/x/sys/windows/registry"
)

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
