from sunny_core.auth_resilience import classify_auth_failure


def test_proxy_authentication_failure_rotates_route() -> None:
    failure = classify_auth_failure("proxy CONNECT failed: HTTP/1.1 407 Proxy Authentication Required")
    assert failure.category == "proxy_authentication"
    assert failure.retryable is True
    assert failure.rotate_proxy is True


def test_curl_connect_tunnel_failure_rotates_route() -> None:
    failure = classify_auth_failure(
        "Failed to perform, curl: (7) CONNECT tunnel failed, response 502"
    )
    assert failure.category == "transient_transport"
    assert failure.retryable is True
    assert failure.rotate_proxy is True
