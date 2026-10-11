package cmd

// Host-side RFB pieces for `preloop desktop serve` (#870).
//
// The endpoint authenticates to the loopback x11vnc itself with the password
// x11vnc stored via -storepasswd, then presents the API relay an RFB 3.8
// server that offers only security type None. The VNC password never leaves
// this host: it is not sent to the API, logged, or written anywhere new.

import (
	"crypto/des"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"os"
)

const rfbVersion38 = "RFB 003.008\n"

const (
	rfbSecurityNone    = 1
	rfbSecurityVNCAuth = 2
)

// vncPasswdObfuscationKey is the fixed key x11vnc/vncpasswd use to obfuscate
// the stored password (as written in the reference implementation; DES there
// reverses the bits of every key byte).
var vncPasswdObfuscationKey = []byte{23, 82, 107, 6, 35, 78, 88, 7}

func reverseBits(b byte) byte {
	var out byte
	for i := 0; i < 8; i++ {
		out = out<<1 | (b>>i)&1
	}
	return out
}

func vncDESKey(key []byte) []byte {
	out := make([]byte, 8)
	for i := 0; i < 8 && i < len(key); i++ {
		out[i] = reverseBits(key[i])
	}
	return out
}

// readVNCPasswordFile decodes an x11vnc -storepasswd file.
func readVNCPasswordFile(path string) ([]byte, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	if len(data) < 8 {
		return nil, errors.New("vnc password file is too short")
	}
	block, err := des.NewCipher(vncDESKey(vncPasswdObfuscationKey))
	if err != nil {
		return nil, err
	}
	plain := make([]byte, 8)
	block.Decrypt(plain, data[:8])
	end := 8
	for end > 0 && plain[end-1] == 0 {
		end--
	}
	return plain[:end], nil
}

// encodeVNCPasswordFile is the inverse of readVNCPasswordFile (tests, fakes).
func encodeVNCPasswordFile(password []byte) []byte {
	block, _ := des.NewCipher(vncDESKey(vncPasswdObfuscationKey))
	plain := make([]byte, 8)
	copy(plain, password)
	out := make([]byte, 8)
	block.Encrypt(out, plain)
	return out
}

// vncAuthResponse encrypts the 16 byte VNC auth challenge with the password.
func vncAuthResponse(password, challenge []byte) ([]byte, error) {
	if len(challenge) != 16 {
		return nil, errors.New("vnc challenge must be 16 bytes")
	}
	block, err := des.NewCipher(vncDESKey(password))
	if err != nil {
		return nil, err
	}
	out := make([]byte, 16)
	block.Encrypt(out[:8], challenge[:8])
	block.Encrypt(out[8:], challenge[8:])
	return out, nil
}

func rfbReadReason(r io.Reader) string {
	var size uint32
	if err := binary.Read(r, binary.BigEndian, &size); err != nil || size > 4096 {
		return "unknown"
	}
	buf := make([]byte, size)
	if _, err := io.ReadFull(r, buf); err != nil {
		return "unknown"
	}
	return string(buf)
}

// rfbClientHandshake runs the RFB 3.8 security handshake against the local
// VNC server, up to and including SecurityResult. ClientInit and everything
// after are left to the relay.
func rfbClientHandshake(rw io.ReadWriter, password []byte) error {
	version := make([]byte, 12)
	if _, err := io.ReadFull(rw, version); err != nil {
		return fmt.Errorf("read server version: %w", err)
	}
	if string(version[:4]) != "RFB " {
		return errors.New("not an RFB server")
	}
	if _, err := rw.Write([]byte(rfbVersion38)); err != nil {
		return err
	}
	count := make([]byte, 1)
	if _, err := io.ReadFull(rw, count); err != nil {
		return err
	}
	if count[0] == 0 {
		return fmt.Errorf("vnc server refused: %s", rfbReadReason(rw))
	}
	types := make([]byte, count[0])
	if _, err := io.ReadFull(rw, types); err != nil {
		return err
	}
	var chosen byte
	for _, t := range types {
		if t == rfbSecurityVNCAuth && len(password) > 0 {
			chosen = t
			break
		}
		if t == rfbSecurityNone && chosen == 0 {
			chosen = t
		}
	}
	if chosen == 0 {
		return errors.New("no supported vnc security type")
	}
	if _, err := rw.Write([]byte{chosen}); err != nil {
		return err
	}
	if chosen == rfbSecurityVNCAuth {
		challenge := make([]byte, 16)
		if _, err := io.ReadFull(rw, challenge); err != nil {
			return err
		}
		response, err := vncAuthResponse(password, challenge)
		if err != nil {
			return err
		}
		if _, err := rw.Write(response); err != nil {
			return err
		}
	}
	var result uint32
	if err := binary.Read(rw, binary.BigEndian, &result); err != nil {
		return err
	}
	if result != 0 {
		return fmt.Errorf("vnc authentication failed: %s", rfbReadReason(rw))
	}
	return nil
}

// rfbServeNoneHandshake presents an RFB 3.8 server with security type None
// to the relay.
func rfbServeNoneHandshake(rw io.ReadWriter) error {
	if _, err := rw.Write([]byte(rfbVersion38)); err != nil {
		return err
	}
	version := make([]byte, 12)
	if _, err := io.ReadFull(rw, version); err != nil {
		return err
	}
	if string(version) != rfbVersion38 && string(version) != "RFB 003.007\n" {
		return fmt.Errorf("unsupported viewer version %q", string(version))
	}
	if _, err := rw.Write([]byte{1, rfbSecurityNone}); err != nil {
		return err
	}
	chosen := make([]byte, 1)
	if _, err := io.ReadFull(rw, chosen); err != nil {
		return err
	}
	if chosen[0] != rfbSecurityNone {
		return errors.New("viewer chose an unsupported security type")
	}
	if string(version) != rfbVersion38 {
		return nil // RFB 3.7 sends no SecurityResult for None
	}
	_, err := rw.Write([]byte{0, 0, 0, 0})
	return err
}
