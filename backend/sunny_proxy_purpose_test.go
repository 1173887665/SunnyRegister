package main

import (
	"strings"
	"testing"

	"github.com/glebarez/sqlite"
	"gorm.io/gorm"
)

func TestNormalizeSunnyProxyPurposes(t *testing.T) {
	got := normalizeSunnyProxyPurposes([]any{"trial", "register", "checkout", "unknown"})
	if strings.Join(got, ",") != "commerce,register" {
		t.Fatalf("purposes=%v", got)
	}
}

func TestSunnyCommerceProxyURLPrefersCheckoutCountryAndPurpose(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:"+strings.ReplaceAll(t.Name(), "/", "-")+"?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&SunnyProxy{}); err != nil {
		t.Fatal(err)
	}
	rows := []SunnyProxy{
		{Address: "http://register.example:8080", Country: "US", PurposeTags: "register", Status: "enabled", Enabled: true, LastCheckOK: true},
		{Address: "http://commerce-de.example:8080", Country: "DE", PurposeTags: "commerce", Status: "enabled", Enabled: true, LastCheckOK: true},
		{Address: "http://commerce-us.example:8080", Country: "US", PurposeTags: "register,commerce", Status: "enabled", Enabled: true, LastCheckOK: true},
	}
	if err := db.Create(&rows).Error; err != nil {
		t.Fatal(err)
	}
	t.Setenv("SUNNY_CHECKOUT_COUNTRY", "US")
	server := &Server{db: db}
	if got := server.sunnyCommerceProxyURL("account@example.com"); got != "http://commerce-us.example:8080" {
		t.Fatalf("proxy=%q", got)
	}
}
