from types import SimpleNamespace

from app.rate_limit import PurchaseLimiter, client_ip


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_per_ip_limit_and_window_expiry():
    clock = FakeClock()
    limiter = PurchaseLimiter(clock=clock)
    for _ in range(10):
        assert limiter.check("1.2.3.4") is None
    assert "10 per hour" in limiter.check("1.2.3.4")
    assert limiter.check("5.6.7.8") is None  # other addresses unaffected
    clock.now += 3600  # oldest attempts leave the one-hour window
    assert limiter.check("1.2.3.4") is None


def test_refused_attempts_are_not_counted():
    clock = FakeClock()
    limiter = PurchaseLimiter(clock=clock)
    for _ in range(10):
        limiter.check("1.2.3.4")
    for _ in range(5):
        assert limiter.check("1.2.3.4") is not None
    clock.now += 3600
    for _ in range(10):
        assert limiter.check("1.2.3.4") is None


def test_overall_daily_limit():
    clock = FakeClock()
    limiter = PurchaseLimiter(clock=clock)
    for i in range(100):
        assert limiter.check(f"10.0.0.{i}") is None
    refusal = limiter.check("10.0.1.1")
    assert "100 purchases per day" in refusal
    clock.now += 86400
    assert limiter.check("10.0.1.1") is None


def test_reset_clears_state():
    limiter = PurchaseLimiter(per_ip=1)
    limiter.check("a")
    assert limiter.check("a") is not None
    limiter.reset()
    assert limiter.check("a") is None


def request(headers=None, host="127.0.0.1"):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host=host))


def test_client_ip_prefers_first_forwarded_for_value():
    assert client_ip(request({"x-forwarded-for": "203.0.113.7, 10.0.0.1"})) == "203.0.113.7"
    assert client_ip(request({"x-forwarded-for": "  203.0.113.7  "})) == "203.0.113.7"


def test_client_ip_falls_back_to_socket_peer():
    assert client_ip(request({}, host="192.0.2.5")) == "192.0.2.5"
    assert client_ip(request({"x-forwarded-for": ""}, host="192.0.2.5")) == "192.0.2.5"
    assert client_ip(SimpleNamespace(headers={}, client=None)) == "unknown"
