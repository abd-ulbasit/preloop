//go:build !darwin && !linux && !windows

package cmd

import (
	"errors"
	"io"
	"os"
)

func validateEnrollmentTokenFile(file *os.File, path string) error {
	return errors.New("unsupported enrollment platform")
}
func protectUnattendedConfig(path string) error { return errors.New("unsupported enrollment platform") }
func runUnattendedAsTarget(opts unattendedOptions, output io.Writer) (bool, error) {
	return true, errors.New("unsupported enrollment platform")
}

func replaceUnattendedConfig(source, target string) error {
	return errors.New("unsupported enrollment platform")
}
