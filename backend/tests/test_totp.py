import base64

import pytest

from reelvault.totp import PERIOD, hotp, match_counter, new_secret

SECRET = base64.b32encode(b"12345678901234567890").decode()


@pytest.mark.parametrize(
    "counter,expected",
    list(
        enumerate(
            [
                "755224",
                "287082",
                "359152",
                "969429",
                "338314",
                "254676",
                "287922",
                "162583",
                "399871",
                "520489",
            ]
        )
    ),
)
def test_rfc4226_hotp_vectors(counter, expected):
    assert hotp(SECRET, counter) == expected


@pytest.mark.parametrize(
    "timestamp,expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc6238_sha1_vectors_and_leading_zeroes(timestamp, expected):
    assert hotp(SECRET, timestamp // PERIOD, digits=8) == expected
    assert match_counter(SECRET, expected[-6:], now=timestamp) == timestamp // PERIOD


def test_skew_window_replay_and_ascii_input():
    counter = 100000
    now = counter * PERIOD + 10
    for candidate in (counter - 1, counter, counter + 1):
        code = hotp(SECRET, candidate)
        assert match_counter(SECRET, code, now=now) == candidate
        assert match_counter(SECRET, code, now=now, last_counter=candidate) is None
    assert match_counter(SECRET, hotp(SECRET, counter - 2), now=now) is None
    assert match_counter(SECRET, hotp(SECRET, counter + 2), now=now) is None
    assert match_counter(SECRET, "１２３４５６", now=now) is None
    assert match_counter(SECRET, "1e+123", now=now) is None
    assert match_counter(SECRET, "12345", now=now) is None
    assert match_counter(SECRET, hotp(SECRET, counter), now=now, last_counter=counter + 1) is None


def test_generated_secret_is_unique_and_future_counters_are_supported():
    first, second = new_secret(), new_secret()
    assert len(first) == 32 and first != second
    assert len(base64.b32decode(first)) == 20
    assert len(hotp(first, 2**32 + 1)) == 6
    with pytest.raises(ValueError):
        hotp(first, -1)
    with pytest.raises(ValueError):
        hotp(first, True)
    with pytest.raises(ValueError):
        hotp("not valid base32", 1)
