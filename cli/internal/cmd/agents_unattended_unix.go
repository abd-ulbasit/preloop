//go:build darwin || linux

package cmd

import (
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"os/user"
	"runtime"
	"strconv"
	"syscall"
)

func validateEnrollmentTokenFile(file *os.File, path string) error {
	info, err := file.Stat()
	if err != nil {
		return err
	}
	named, err := os.Lstat(path)
	if err != nil || !os.SameFile(info, named) || named.Mode()&os.ModeSymlink != 0 {
		return errors.New("enrollment token symlinks are not supported")
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || info.Mode().Perm()&0077 != 0 || (stat.Uid != uint32(os.Geteuid()) && stat.Uid != 0) {
		return errors.New("enrollment token must be owner-only (0600), owned by the caller or root")
	}
	return nil
}

func protectUnattendedConfig(path string) error { return os.Chmod(path, 0600) }

func runUnattendedAsTarget(opts unattendedOptions, output io.Writer) (bool, error) {
	current, err := user.Current()
	if err != nil {
		return false, err
	}
	var console *user.User
	if runtime.GOOS == "darwin" && os.Geteuid() == 0 {
		info, err := os.Stat("/dev/console")
		if err != nil {
			return true, err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok {
			return true, errors.New("cannot resolve console user")
		}
		if stat.Uid >= 500 && stat.Uid != 65534 {
			console, err = user.LookupId(strconv.FormatUint(uint64(stat.Uid), 10))
			if err != nil {
				return true, err
			}
		}
	}
	target, pending, err := resolveUnattendedUnixTarget(runtime.GOOS, os.Geteuid(), current, opts.TargetUser, console)
	if err != nil {
		return false, err
	}
	if pending {
		fmt.Fprintln(output, "pending_user: no logged-in console user; authority remains unused") //nolint:errcheck
		return true, nil
	}
	if os.Geteuid() != 0 {
		if os.Getenv("HOME") != current.HomeDir {
			return false, errors.New("HOME does not match the target OS user")
		}
		return false, nil
	}
	selected, err := user.Lookup(target)
	if err != nil {
		selected, err = user.LookupId(target)
	}
	if err != nil {
		return true, errors.New("target OS user was not found")
	}
	uid, err := strconv.ParseUint(selected.Uid, 10, 32)
	if err != nil || uid < 500 || uid == 65534 {
		return true, errors.New("target must be a non-system OS user")
	}
	gid, err := strconv.ParseUint(selected.Gid, 10, 32)
	if err != nil {
		return true, err
	}
	opts.TargetUser = selected.Username
	child, err := unattendedChild(opts, output)
	if err != nil {
		return true, err
	}
	child.Dir = selected.HomeDir
	child.Env = []string{"HOME=" + selected.HomeDir, "USER=" + selected.Username, "LOGNAME=" + selected.Username, "PATH=/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin", "PRELOOP_DISABLE_TELEMETRY=true"}
	if runtime.GOOS == "darwin" {
		// asuser selects the login bootstrap/keychain session; sudo drops root.
		commandArgs := append([]string{"asuser", selected.Uid, "/usr/bin/sudo", "-H", "-u", "#" + selected.Uid, "--", child.Path}, child.Args[1:]...)
		wrapper := exec.Command("/bin/launchctl", commandArgs...)
		wrapper.Env, wrapper.Dir, wrapper.Stdin, wrapper.Stdout, wrapper.Stderr = child.Env, child.Dir, child.Stdin, child.Stdout, child.Stderr
		return true, wrapper.Run()
	}
	// Secret Service belongs to the target login session, not root's bus.
	runtimeDir := "/run/user/" + selected.Uid
	busPath := runtimeDir + "/bus"
	if busInfo, err := os.Stat(busPath); err == nil {
		if stat, ok := busInfo.Sys().(*syscall.Stat_t); ok && stat.Uid == uint32(uid) && busInfo.Mode()&os.ModeSocket != 0 {
			child.Env = append(child.Env, "XDG_RUNTIME_DIR="+runtimeDir, "DBUS_SESSION_BUS_ADDRESS=unix:path="+busPath)
		}
	}
	groupIDs, err := selected.GroupIds()
	if err != nil {
		return true, err
	}
	groups := make([]uint32, 0, len(groupIDs))
	for _, value := range groupIDs {
		groupID, err := strconv.ParseUint(value, 10, 32)
		if err != nil {
			return true, err
		}
		groups = append(groups, uint32(groupID))
	}
	child.SysProcAttr = &syscall.SysProcAttr{Credential: &syscall.Credential{Uid: uint32(uid), Gid: uint32(gid), Groups: groups}}
	return true, child.Run()
}

func replaceUnattendedConfig(source, target string) error { return os.Rename(source, target) }

// Resolve the OS identity before reading provisioning authority or app files.
func resolveUnattendedUnixTarget(goos string, euid int, current *user.User, explicit string, console *user.User) (string, bool, error) {
	if goos == "linux" && explicit == "" {
		return "", false, errors.New("Linux unattended enrollment requires --target-user")
	}
	matches := func(candidate *user.User) bool {
		return explicit == "" || explicit == candidate.Username || explicit == candidate.Uid
	}
	if euid != 0 {
		if !matches(current) {
			return "", false, errors.New("target user differs from the executing user")
		}
		return current.Username, false, nil
	}
	if goos == "darwin" {
		if console == nil {
			return "", true, nil
		}
		uid, err := strconv.ParseUint(console.Uid, 10, 32)
		if err != nil || uid < 500 || uid == 65534 {
			return "", true, nil
		}
		if !matches(console) {
			return "", false, errors.New("explicit target does not match the console user")
		}
		return console.Username, false, nil
	}
	return explicit, false, nil
}
