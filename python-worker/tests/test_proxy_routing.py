from sunny_core.worker import _auxiliary_proxy


def test_auxiliary_proxy_defaults_to_direct_server_egress():
    payload = {}
    proxies = {"register": "http://pool.example:1000", "local_proxy": "http://127.0.0.1:7897", "mode": "proxy_pool"}

    assert _auxiliary_proxy(payload, proxies) == ""


def test_auxiliary_proxy_can_route_all_traffic_through_pool():
    payload = {"proxy_all_traffic": True}
    proxies = {"register": "http://pool.example:1000", "local_proxy": "http://127.0.0.1:7897", "mode": "proxy_pool"}

    assert _auxiliary_proxy(payload, proxies) == "http://pool.example:1000"
