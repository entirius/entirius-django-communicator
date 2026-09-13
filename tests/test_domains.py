# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import pytest

from django_communicator.utils.domains import email_domain, registrable_domain


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("www.shop.pl", "shop.pl"),
        ("SHOP.PL.", "shop.pl"),
        ("a.b.shop.com.pl", "shop.com.pl"),
        ("myshop.pl", "myshop.pl"),
    ],
)
def test_registrable_domain(host, expected):
    assert registrable_domain(host) == expected


def test_email_domain_requires_a_host():
    assert email_domain("Jan@Sklep.Shop.pl") == "shop.pl"
    with pytest.raises(ValueError):
        email_domain("jan@")
