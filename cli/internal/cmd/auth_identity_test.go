package cmd

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/preloop/preloop/cli/internal/api"
)

func TestFetchUserInfoPreservesCanonicalPersonAndMembership(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == authUserInfoPath {
			_, _ = w.Write([]byte(`{"id":"membership-child","person_id":"person-example","account_id":"account-child","username":"chosen-person","email":"jane@example.com","full_name":"Jane Example"}`))
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()
	var info UserInfo
	if err := fetchUserInfo(api.NewClientWithToken(server.URL, "test-token"), &info); err != nil {
		t.Fatal(err)
	}
	if info.ID != "membership-child" || info.PersonID != "person-example" || info.AccountID != "account-child" {
		t.Fatalf("identity and membership context changed: %#v", info)
	}
	if info.Name != "Jane Example" || info.Username != "chosen-person" {
		t.Fatalf("expected canonical profile even without EE enrichment: %#v", info)
	}
}
