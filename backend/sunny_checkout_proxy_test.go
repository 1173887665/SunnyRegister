package main

import "testing"

func TestNormalizeCheckoutProxyUsesKookeeySocksProtocol(t *testing.T) {
	proxy, err := normalizeCheckoutProxy("gate.kookeey.info:1000:user:password-DE-session")
	if err != nil {
		t.Fatalf("normalize checkout proxy: %v", err)
	}
	if proxy != "socks5h://user:password-DE-session@gate.kookeey.info:1000" {
		t.Fatalf("proxy = %q", proxy)
	}
}

func TestNormalizeCheckoutProxyKeepsOrdinaryHTTPProtocol(t *testing.T) {
	proxy, err := normalizeCheckoutProxy("proxy.example.com:8080:user:password")
	if err != nil {
		t.Fatalf("normalize checkout proxy: %v", err)
	}
	if proxy != "http://user:password@proxy.example.com:8080" {
		t.Fatalf("proxy = %q", proxy)
	}
}
