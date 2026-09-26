# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""A template targets an audience (leads: the lead type) — the language × audience cascade, most specific first."""

import importlib

import pytest
from django.apps import apps
from django.core.exceptions import ValidationError
from django.db.migrations.exceptions import IrreversibleError

from django_communicator.services import template_service
from django_communicator.services.communicate_service import communicate
from tests.conftest import CHANNEL_IDX, api_url
from tests.factories import LanguageFactory, make_static

KEY = "lead.cold"


@pytest.fixture
def english(db):
    return LanguageFactory(iso2="EN")


def variant(channel, language, audience: str, subject: str):
    return make_static(channel, key=KEY, language=language, audience=audience, subject=subject)


def resolved(channel, language_code: str, audience: str = "") -> str:
    return template_service.resolve(channel, KEY, language_code, audience).subject


def test_resolve_walks_language_and_audience_from_most_specific(channel, english):
    default = channel.default_language  # PL
    variant(channel, english, "RETAILER", "en retailer")
    variant(channel, english, "", "en any")
    variant(channel, default, "RETAILER", "pl retailer")
    variant(channel, default, "", "pl any")

    assert resolved(channel, "en", "RETAILER") == "en retailer"  # 1. language + audience
    assert resolved(channel, "en", "WHOLESALE") == "en any"  # 2. language, any audience
    assert resolved(channel, "en") == "en any"  # blank audience never picks a variant


def test_resolve_falls_back_to_the_channel_default_language(channel, english):
    default = channel.default_language
    variant(channel, default, "RETAILER", "pl retailer")
    variant(channel, default, "", "pl any")

    assert resolved(channel, "de", "RETAILER") == "pl retailer"  # 3. default language + audience
    assert resolved(channel, "de", "WHOLESALE") == "pl any"  # 4. default language, any audience


def test_resolve_without_a_matching_variant_or_default_raises(channel, english):
    variant(channel, english, "RETAILER", "en retailer")

    with pytest.raises(template_service.NoTemplateError):
        resolved(channel, "en", "WHOLESALE")
    with pytest.raises(template_service.NoTemplateError):
        resolved(channel, "en")


def test_communicate_passes_the_audience_to_the_cascade(channel, recipient, context):
    variant(channel, channel.default_language, "RETAILER", "Hi retailer {first_name}")
    variant(channel, channel.default_language, "", "Hi {first_name}")

    def subject(audience: str) -> str:
        message = communicate(
            channel_idx=CHANNEL_IDX,
            template_key=KEY,
            recipient=recipient,
            context=context,
            subject_ref=f"bdd:audience-{audience}",
            audience=audience,
        )
        return message.subject

    assert subject("RETAILER") == "Hi retailer Jan"
    assert subject("MANUFACTURER") == "Hi Jan"


def test_audience_is_an_upper_case_code_and_part_of_the_unique_key(channel):
    variant(channel, channel.default_language, "RETAILER", "a")
    variant(channel, channel.default_language, "", "b")

    with pytest.raises(ValidationError):
        variant(channel, channel.default_language, "RETAILER", "duplicate")
    with pytest.raises(ValidationError):
        variant(channel, channel.default_language, "retailer", "lower case")


def test_put_without_audience_keeps_it_and_an_explicit_blank_clears_it(admin_api, channel):
    template = variant(channel, channel.default_language, "RETAILER", "a")
    url = api_url(f"templates/{template.pk}/")
    body = {"key": KEY, "kind": "static", "language": "pl", "subject": "a", "body": "Following up."}

    assert admin_api.put(url, body, format="json").json()["audience"] == "RETAILER"
    assert admin_api.put(url, {**body, "audience": ""}, format="json").json()["audience"] == ""


def test_migration_0010_reverse_refuses_while_audience_variants_exist(channel):
    migration = importlib.import_module("django_communicator.migrations.0010_template_audience")
    blank = make_static(channel, key=KEY, audience="")
    migration.refuse_audience_variants(apps, None)  # blank audiences only: reversible

    make_static(channel, key=KEY, language=blank.language, audience="RETAILER")

    with pytest.raises(IrreversibleError, match="Delete the audience variants"):
        migration.refuse_audience_variants(apps, None)
