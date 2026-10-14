package cmd

import (
	"io"
	"os"
	"path/filepath"
	"strings"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/registry"
)

func inventoryElevated() bool { return windows.GetCurrentProcessToken().IsElevated() }
func localInventoryProfiles() ([]inventoryProfile, bool, error) {
	key, err := registry.OpenKey(registry.LOCAL_MACHINE, `SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList`, registry.READ|registry.WOW64_64KEY)
	if err != nil {
		return nil, false, err
	}
	defer key.Close()
	// Read one extra entry to detect the bounded enumeration limit. This
	// includes service SIDs in the bound; excluded entries do not hide truncation.
	names, err := key.ReadSubKeyNames(inventoryProfileLimit + 1)
	if err != nil && err != io.EOF {
		return nil, false, err
	}
	return inventoryWindowsProfiles(names, func(sid string) (string, error) {
		sub, err := registry.OpenKey(key, sid, registry.QUERY_VALUE)
		if err != nil {
			return "", err
		}
		defer sub.Close()
		path, kind, err := sub.GetStringValue("ProfileImagePath")
		if err != nil {
			return "", err
		}
		if kind == registry.EXPAND_SZ {
			return registry.ExpandString(path)
		}
		return path, nil
	}, func(volume string) bool {
		pointer, err := windows.UTF16PtrFromString(volume + `\`)
		return err == nil && windows.GetDriveType(pointer) == windows.DRIVE_FIXED
	})
}

// The reader and drive classifier are injected so native Windows CI tests
// exercise ProfileList semantics using fixtures, never the host registry.
func inventoryWindowsProfiles(names []string, read func(string) (string, error), localDrive func(string) bool) ([]inventoryProfile, bool, error) {
	profiles := []inventoryProfile{}
	truncated := len(names) > inventoryProfileLimit
	if truncated {
		names = names[:inventoryProfileLimit]
	}
	seen := map[string]bool{}
	for _, sid := range names {
		if !strings.HasPrefix(sid, "S-1-5-21-") && !strings.HasPrefix(sid, "S-1-12-1-") {
			continue
		}
		path, err := read(sid)
		if err != nil {
			return profiles, truncated, err
		}
		volume := filepath.VolumeName(path)
		if !filepath.IsAbs(path) || strings.HasPrefix(path, `\\`) || len(volume) != 2 || strings.ContainsAny(path[2:], ":\x00") || !localDrive(volume) {
			return profiles, truncated, os.ErrPermission
		}
		path = filepath.Clean(path)
		normalized := strings.ToLower(path)
		if !seen[normalized] {
			profiles = append(profiles, inventoryProfile{path})
			seen[normalized] = true
		}
	}
	return profiles, truncated, nil
}

func openInventoryConfig(root *os.Root, path string) (*os.File, error) { return root.Open(path) }
