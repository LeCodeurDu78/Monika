"""Le lien du téléphone : un nom MagicDNS, le port HTTPS, le jeton une seule fois."""

from __future__ import annotations

import pytest

from monika.phone import PhoneError, dns_name, serve_link


def test_dns_name_drops_the_trailing_dot() -> None:
    payload = '{"Self": {"DNSName": "archlinux.tail216c90.ts.net."}}'

    assert dns_name(payload) == "archlinux.tail216c90.ts.net"


def test_dns_name_explains_a_missing_name() -> None:
    with pytest.raises(PhoneError):
        dns_name('{"Self": {"DNSName": ""}}')


def test_dns_name_refuses_broken_json() -> None:
    with pytest.raises(PhoneError):
        dns_name("pas du JSON")


def test_link_is_https_on_443_with_the_token() -> None:
    assert serve_link("archlinux.tail216c90.ts.net", "secret") == (
        "https://archlinux.tail216c90.ts.net/?token=secret"
    )


def test_link_names_another_https_port() -> None:
    assert serve_link("monika.ts.net", "secret", 8443) == (
        "https://monika.ts.net:8443/?token=secret"
    )


def test_without_a_token_the_link_stays_valid() -> None:
    assert serve_link("monika.ts.net", "") == "https://monika.ts.net/"
