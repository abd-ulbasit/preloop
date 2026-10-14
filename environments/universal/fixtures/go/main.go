package main

import (
	"fmt"
	"os"
)

func greet(name string) string {
	return "hello " + name
}

func main() {
	if _, err := fmt.Println(greet("world")); err != nil {
		os.Exit(1)
	}
}
