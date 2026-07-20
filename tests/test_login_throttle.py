"""Login throttling and the password floor."""
import server
from conftest import register, login


def test_password_floor_enforced_on_register(client):
    resp = client.post("/api/auth/register", json={"name": "Alice", "password": "short7!"})
    assert resp.status_code == 400
    # exactly the minimum is fine
    ok = client.post("/api/auth/register",
                     json={"name": "Alice", "password": "x" * server.MIN_PASSWORD_LEN})
    assert ok.status_code == 201


def test_repeated_wrong_passwords_get_throttled(client):
    register(client, "Alice", password="correcthorse")
    # burn through the allowance with wrong guesses
    for _ in range(server.LOGIN_MAX_FAILS):
        r = client.post("/api/auth/login", json={"name": "Alice", "password": "nope"})
        assert r.status_code == 401
    # the next attempt — even with the *right* password — is refused
    blocked = client.post("/api/auth/login", json={"name": "Alice", "password": "correcthorse"})
    assert blocked.status_code == 429


def test_throttle_is_per_account(client):
    register(client, "Alice", password="correcthorse")
    register(client, "Bob", password="correcthorse")
    for _ in range(server.LOGIN_MAX_FAILS):
        client.post("/api/auth/login", json={"name": "Alice", "password": "nope"})
    # Alice is throttled; Bob is untouched
    assert client.post("/api/auth/login",
                       json={"name": "Alice", "password": "correcthorse"}).status_code == 429
    assert login(client, "Bob", password="correcthorse")


def test_successful_login_resets_the_counter(client):
    register(client, "Alice", password="correcthorse")
    for _ in range(server.LOGIN_MAX_FAILS - 1):  # one short of the limit
        client.post("/api/auth/login", json={"name": "Alice", "password": "nope"})
    # a good login clears the slate...
    assert login(client, "Alice", password="correcthorse")
    # ...so the counter starts fresh and a new wrong guess isn't blocked
    assert client.post("/api/auth/login",
                       json={"name": "Alice", "password": "nope"}).status_code == 401
