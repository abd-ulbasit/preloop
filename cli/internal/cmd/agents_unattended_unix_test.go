//go:build darwin || linux

package cmd

import (
	"os"
	"os/user"
	"path/filepath"
	"testing"
)

func TestUnattendedUnixTargetSelection(t *testing.T) {
	jane := &user.User{Uid: "1001", Username: "jane", HomeDir: "/home/jane"}
	console := &user.User{Uid: "501", Username: "jane", HomeDir: "/Users/jane"}
	root := &user.User{Uid: "0", Username: "root"}
	for _, test := range []struct {
		name, goos    string
		euid          int
		current       *user.User
		target        string
		console       *user.User
		want          string
		pending, fail bool
	}{
		{name: "Linux needs explicit target", goos: "linux", euid: 0, current: root, fail: true},
		{name: "Linux root target", goos: "linux", euid: 0, current: root, target: "jane", want: "jane"},
		{name: "Linux ordinary explicit self", goos: "linux", euid: 1001, current: jane, target: "1001", want: "jane"},
		{name: "ordinary cross-user denied", goos: "linux", euid: 1001, current: jane, target: "another", fail: true},
		{name: "macOS console", goos: "darwin", euid: 0, current: root, console: console, want: "jane"},
		{name: "macOS console mismatch", goos: "darwin", euid: 0, current: root, target: "another", console: console, fail: true},
		{name: "macOS pending user", goos: "darwin", euid: 0, current: root, pending: true},
		{name: "macOS loginwindow", goos: "darwin", euid: 0, current: root, console: root, pending: true},
		{name: "macOS current session", goos: "darwin", euid: 501, current: console, want: "jane"},
	} {
		t.Run(test.name, func(t *testing.T) {
			actual, pending, err := resolveUnattendedUnixTarget(test.goos, test.euid, test.current, test.target, test.console)
			if (err != nil) != test.fail || actual != test.want || pending != test.pending {
				t.Fatalf("got %q pending=%v err=%v", actual, pending, err)
			}
		})
	}
}

func TestUnattendedAuthorityFileRejectsSharedAndSymlinkedFiles(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "authority")
	if err := os.WriteFile(path, []byte("synthetic"), 0644); err != nil {
		t.Fatal(err)
	}
	file, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if err := validateEnrollmentTokenFile(file, path); err == nil {
		t.Fatal("accepted readable-by-others authority")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := validateEnrollmentTokenFile(file, path); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(dir, "link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if err := validateEnrollmentTokenFile(file, link); err == nil {
		t.Fatal("accepted symlink authority")
	}
}
