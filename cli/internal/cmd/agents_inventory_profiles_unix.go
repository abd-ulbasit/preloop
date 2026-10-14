//go:build !windows

package cmd

import (
	"errors"
	"os"
	"runtime"

	"golang.org/x/sys/unix"
)

func inventoryElevated() bool { return os.Geteuid() == 0 }
func localInventoryProfiles() ([]inventoryProfile, bool, error) {
	switch runtime.GOOS {
	case "darwin":
		return directoryInventoryProfiles("/Users", false)
	case "linux":
		return directoryInventoryProfiles("/home", true)
	default:
		return nil, false, errors.New("unsupported platform")
	}
}

func openInventoryConfig(root *os.Root, path string) (*os.File, error) {
	return root.OpenFile(path, os.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW, 0)
}
