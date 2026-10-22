from datetime import timedelta

import pytest

from app.core.errors import AuthenticationError
from app.core.security import PasswordHasher, TokenCodec, generate_opaque_token, hash_token


def test_hash_and_verify_password() -> None:
    h = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)  # cheap params in tests
    digest = h.hash("correct horse battery")
    assert digest.startswith("$argon2id$")
    assert h.verify(digest, "correct horse battery") is True
    assert h.verify(digest, "wrong") is False


def test_verify_unknown_user_burns_dummy_hash() -> None:
    h = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    assert h.verify(None, "anything") is False  # dummy hash path, never raises


def test_verify_malformed_hash_is_false() -> None:
    h = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    assert h.verify("not-a-hash", "x") is False


def test_production_params_are_argon2id_t3_m64mib_p4() -> None:
    h = PasswordHasher()
    assert (h.time_cost, h.memory_cost, h.parallelism) == (3, 65536, 4)


def test_needs_rehash_when_params_change() -> None:
    cheap = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
    digest = cheap.hash("pw-long-enough-123")
    assert cheap.needs_rehash(digest) is False
    assert PasswordHasher(time_cost=2, memory_cost=8, parallelism=1).needs_rehash(digest) is True


def test_jwt_round_trip_and_kid_rotation() -> None:
    old = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    token = old.encode_access(sub="u1", role="player", trainer_id=None, ttl=timedelta(minutes=15))
    rotated = TokenCodec({"k1": "a" * 32, "k2": "b" * 32}, current_kid="k2")
    claims = rotated.decode_access(token)  # previous key still accepted
    assert claims["sub"] == "u1" and claims["typ"] == "access" and "jti" in claims


def test_jwt_carries_act_claim() -> None:
    c = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    token = c.encode_access(
        sub="u", role="player", trainer_id=None, ttl=timedelta(minutes=5), act="admin-1", jti="j1"
    )
    claims = c.decode_access(token)
    assert claims["act"] == "admin-1" and claims["jti"] == "j1"


def test_expired_or_tampered_jwt_is_401() -> None:
    c = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    expired = c.encode_access(sub="u", role="player", trainer_id=None, ttl=timedelta(seconds=-1))
    with pytest.raises(AuthenticationError):
        c.decode_access(expired)
    fresh = c.encode_access(sub="u", role="player", trainer_id=None, ttl=timedelta(minutes=1))
    with pytest.raises(AuthenticationError):
        c.decode_access(fresh[:-2] + "xx")


def test_unknown_kid_and_garbage_are_401() -> None:
    a = TokenCodec({"k1": "a" * 32}, current_kid="k1")
    b = TokenCodec({"k9": "a" * 32}, current_kid="k9")
    token = b.encode_access(sub="u", role="player", trainer_id=None, ttl=timedelta(minutes=1))
    with pytest.raises(AuthenticationError) as e:
        a.decode_access(token)
    assert e.value.code == "INVALID_TOKEN"
    with pytest.raises(AuthenticationError):
        a.decode_access("garbage")


def test_opaque_token_is_43_chars_and_hash_is_stable() -> None:
    t = generate_opaque_token()
    assert len(t) == 43
    assert hash_token(t) == hash_token(t) and hash_token(t) != t
