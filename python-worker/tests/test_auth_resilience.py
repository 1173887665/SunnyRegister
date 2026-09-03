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


def test_common_recoverable_transport_errors_share_the_same_retry_bucket() -> None:
    for message in (
        "HTTP 503 Service Unavailable",
        "connection reset by peer",
        "temporary failure in name resolution",
        "ECONNRESET while reading response",
        "tls handshake timeout",
    ):
        failure = classify_auth_failure(message)
        assert failure.category == "transient_transport"
        assert failure.retryable is True
        assert failure.rotate_proxy is True


def test_rate_limit_keeps_proxy_rotation_but_uses_separate_bucket() -> None:
    failure = classify_auth_failure("HTTP 429 Too Many Requests")
    assert failure.category == "rate_limited"
    assert failure.retryable is True
    assert failure.rotate_proxy is True


def test_stale_auth_context_rebuilds_and_rotates_route() -> None:
    failure = classify_auth_failure("OpenAI auth failed: invalid_state")
    assert failure.category == "stale_auth_context"
    assert failure.retryable is True
    assert failure.fresh_context is True
    assert failure.rotate_proxy is True
