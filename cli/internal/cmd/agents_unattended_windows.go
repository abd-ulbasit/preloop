//go:build windows

package cmd

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/user"
	"path/filepath"
	"sort"
	"strings"
	"unsafe"

	"golang.org/x/sys/windows"
)

func validateEnrollmentTokenFile(file *os.File, path string) error {
	info, err := os.Lstat(path)
	if err != nil || info.Mode()&os.ModeSymlink != 0 {
		return errors.New("enrollment token must not be a symlink")
	}
	current, err := user.Current()
	if err != nil {
		return err
	}
	descriptor, err := windows.GetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.OWNER_SECURITY_INFORMATION|windows.DACL_SECURITY_INFORMATION)
	if err != nil {
		return err
	}
	owner, _, err := descriptor.Owner()
	if err != nil || !enrollmentTrustedSID(owner.String(), current.Uid) {
		return errors.New("enrollment token owner is not trusted")
	}
	dacl, _, err := descriptor.DACL()
	if err != nil || dacl == nil {
		return errors.New("enrollment token requires a restricted DACL")
	}
	for index := uint32(0); index < uint32(dacl.AceCount); index++ {
		var ace *windows.ACCESS_ALLOWED_ACE
		if err := windows.GetAce(dacl, index, &ace); err != nil {
			return err
		}
		if ace.Header.AceType == windows.ACCESS_DENIED_ACE_TYPE {
			continue
		}
		if ace.Header.AceType != windows.ACCESS_ALLOWED_ACE_TYPE {
			return errors.New("unsupported enrollment token ACL entry")
		}
		sid := (*windows.SID)(unsafe.Pointer(&ace.SidStart))
		if !enrollmentTrustedSID(sid.String(), current.Uid) {
			return errors.New("enrollment token ACL grants another principal access")
		}
	}
	return nil
}

func protectUnattendedConfig(path string) error {
	current, err := user.Current()
	if err != nil {
		return err
	}
	descriptor, err := windows.SecurityDescriptorFromString("D:P(A;;FA;;;" + current.Uid + ")")
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil)
}

func runUnattendedAsTarget(opts unattendedOptions, output io.Writer) (bool, error) {
	current, err := user.Current()
	if err != nil {
		return false, err
	}
	if current.Uid != "S-1-5-18" {
		if opts.TargetUser != "" && opts.TargetUser != current.Username && opts.TargetUser != current.Uid {
			return false, errors.New("target differs from the executing user")
		}
		return false, nil
	}
	var sessions *windows.WTS_SESSION_INFO
	var count uint32
	if err := windows.WTSEnumerateSessions(0, 0, 1, &sessions, &count); err != nil {
		return true, err
	}
	defer windows.WTSFreeMemory(uintptr(unsafe.Pointer(sessions)))
	if count > 1024 {
		return true, errors.New("too many sessions to resolve target safely")
	}
	active := []uint32{}
	for _, session := range unsafe.Slice(sessions, count) {
		if session.State == 0 && session.SessionID != 0 {
			active = append(active, session.SessionID)
		}
	}
	sessionID, pending, err := selectUnattendedWindowsSession(active)
	if err != nil {
		return true, err
	}
	if pending {
		fmt.Fprintln(output, "pending_user: no active user session; authority remains unused") //nolint:errcheck
		return true, nil
	}
	var token windows.Token
	if err := windows.WTSQueryUserToken(sessionID, &token); err != nil {
		return true, err
	}
	defer token.Close()
	tokenUser, err := token.GetTokenUser()
	if err != nil {
		return true, err
	}
	targetSID := tokenUser.User.Sid.String()
	if targetSID == "S-1-5-18" || targetSID == "S-1-5-19" || targetSID == "S-1-5-20" {
		return true, errors.New("active session is a system principal")
	}
	selected, err := user.LookupId(targetSID)
	if err != nil {
		return true, err
	}
	if opts.TargetUser != "" && opts.TargetUser != selected.Username && opts.TargetUser != targetSID {
		return true, errors.New("explicit target does not match the active session")
	}
	profile, err := token.GetUserProfileDirectory()
	if err != nil {
		return true, err
	}
	env, err := token.Environ(false)
	if err != nil {
		return true, err
	}
	opts.TargetUser = targetSID
	child, err := unattendedChild(opts, output)
	if err != nil {
		return true, err
	}
	// Windows forbids inheriting stdin/stdout handles across sessions. Stage
	// the sanitized authority under a SYSTEM-owned DACL, then launch without
	// inherited handles. The target user receives read access only.
	stageDir, err := os.MkdirTemp("", "preloop-enrollment-*")
	if err != nil {
		return true, err
	}
	defer os.RemoveAll(stageDir) //nolint:errcheck
	if err := setEnrollmentWindowsDACL(stageDir, targetSID, true); err != nil {
		return true, err
	}
	stagePath := filepath.Join(stageDir, "authority.json")
	file, err := os.OpenFile(stagePath, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return true, err
	}
	if err := setEnrollmentWindowsDACL(stagePath, targetSID, false); err != nil {
		_ = file.Close()
		return true, err
	}
	// A consumed/removed authority must not prevent an unchanged rerun. With
	// no checkpoint, an empty staged file fails closed in the target process.
	raw, readErr := readEnrollmentAuthority(opts.TokenFile)
	if readErr == nil {
		_, err = file.Write(raw)
	}
	closeErr := file.Close()
	if err != nil {
		return true, err
	}
	if closeErr != nil {
		return true, closeErr
	}
	for index, arg := range child.Args {
		if arg == "--enrollment-token-file" && index+1 < len(child.Args) {
			child.Args[index+1] = stagePath
			break
		}
	}
	if err := launchEnrollmentWindowsUser(token, child.Path, child.Args, profile, env); err != nil {
		return true, fmt.Errorf("enrollment in target user session failed: %w", err)
	}
	fmt.Fprintln(output, "Selected apps processed in the active user session; application login may still be pending.") //nolint:errcheck
	return true, nil
}

func enrollmentTrustedSID(sid, current string) bool {
	return sid == current || sid == "S-1-5-18" || sid == "S-1-5-32-544"
}

func replaceUnattendedConfig(source, target string) error {
	sourcePtr, err := windows.UTF16PtrFromString(source)
	if err != nil {
		return err
	}
	targetPtr, err := windows.UTF16PtrFromString(target)
	if err != nil {
		return err
	}
	return windows.MoveFileEx(sourcePtr, targetPtr, windows.MOVEFILE_REPLACE_EXISTING|windows.MOVEFILE_WRITE_THROUGH)
}

func setEnrollmentWindowsDACL(path, targetSID string, directory bool) error {
	permissions := "GR"
	inheritance := ""
	if directory {
		permissions = "GRGX"
		inheritance = "OICI"
	}
	descriptor, err := windows.SecurityDescriptorFromString("D:P(A;" + inheritance + ";FA;;;SY)(A;" + inheritance + ";" + permissions + ";;;" + targetSID + ")")
	if err != nil {
		return err
	}
	dacl, _, err := descriptor.DACL()
	if err != nil {
		return err
	}
	return windows.SetNamedSecurityInfo(path, windows.SE_FILE_OBJECT, windows.DACL_SECURITY_INFORMATION|windows.PROTECTED_DACL_SECURITY_INFORMATION, nil, nil, dacl, nil)
}

// CreateProcessAsUser needs the target interactive desktop explicitly. A
// service's default window station belongs to session 0 and must not leak
// into the logged-in user's process. No handles cross the session boundary.
func launchEnrollmentWindowsUser(token windows.Token, executable string, args []string, profile string, environment []string) error {
	appPtr, err := windows.UTF16PtrFromString(executable)
	if err != nil {
		return err
	}
	commandPtr, err := windows.UTF16PtrFromString(windows.ComposeCommandLine(args))
	if err != nil {
		return err
	}
	directoryPtr, err := windows.UTF16PtrFromString(profile)
	if err != nil {
		return err
	}
	desktopPtr, err := windows.UTF16PtrFromString(`winsta0\default`)
	if err != nil {
		return err
	}
	envBlock, err := enrollmentWindowsEnvironment(environment)
	if err != nil {
		return err
	}
	startup := windows.StartupInfo{Desktop: desktopPtr, Flags: windows.STARTF_USESHOWWINDOW, ShowWindow: windows.SW_HIDE}
	startup.Cb = uint32(unsafe.Sizeof(startup))
	var process windows.ProcessInformation
	if err := windows.CreateProcessAsUser(token, appPtr, commandPtr, nil, nil, false, windows.CREATE_UNICODE_ENVIRONMENT|windows.CREATE_NO_WINDOW, &envBlock[0], directoryPtr, &startup, &process); err != nil {
		return err
	}
	defer windows.CloseHandle(process.Thread)  //nolint:errcheck
	defer windows.CloseHandle(process.Process) //nolint:errcheck
	event, err := windows.WaitForSingleObject(process.Process, 300000)
	if err != nil {
		return err
	}
	if event != windows.WAIT_OBJECT_0 {
		_ = windows.TerminateProcess(process.Process, 1)
		_, _ = windows.WaitForSingleObject(process.Process, 5000)
		return errors.New("target enrollment process did not finish within five minutes; retry from its credential checkpoint")
	}
	var code uint32
	if err := windows.GetExitCodeProcess(process.Process, &code); err != nil {
		return err
	}
	if code != 0 {
		return fmt.Errorf("target enrollment exited with status %d", code)
	}
	return nil
}

func enrollmentWindowsEnvironment(values []string) ([]uint16, error) {
	filtered := make([]string, 0, len(values)+1)
	for _, value := range values {
		if !strings.HasPrefix(strings.ToUpper(value), "PRELOOP_DISABLE_TELEMETRY=") {
			filtered = append(filtered, value)
		}
	}
	filtered = append(filtered, "PRELOOP_DISABLE_TELEMETRY=true")
	sort.Slice(filtered, func(i, j int) bool { return strings.ToUpper(filtered[i]) < strings.ToUpper(filtered[j]) })
	block := []uint16{}
	for _, value := range filtered {
		encoded, err := windows.UTF16FromString(value)
		if err != nil {
			return nil, err
		}
		block = append(block, encoded...)
	}
	return append(block, 0), nil
}
