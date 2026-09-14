# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
import factory
from django_regional.models import Language

from django_communicator.enums import TemplateKind
from django_communicator.models import Channel, MessageTemplate
from django_communicator.services import template_service

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body_paragraphs": {"type": "array", "items": {"type": "string"}}},
    "required": ["subject", "body_paragraphs"],
}


class LanguageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Language
        django_get_or_create = ("iso2",)

    iso2 = "PL"
    iso3 = factory.LazyAttribute(lambda o: {"PL": "POL", "EN": "ENG", "DE": "DEU"}[o.iso2])
    name_en = factory.LazyAttribute(lambda o: o.iso2)
    name_pl = factory.LazyAttribute(lambda o: o.iso2)


class ChannelFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Channel
        django_get_or_create = ("idx",)

    idx = "default-europe"
    label = "Default Europe"
    default_language = factory.SubFactory(LanguageFactory)


def make_template(channel: Channel, **overrides) -> MessageTemplate:
    """An ai_prompt template saved through the versioning service (version 1)."""
    fields = {
        "key": "lead.cold.shop",
        "kind": TemplateKind.AI_PROMPT,
        "language": channel.default_language,
        "subject": "Cold shop",
        "body": "You write short emails.\n=== USER ===\nWrite to {first_name} at {company_name}.",
        "json_schema": DRAFT_SCHEMA,
        "model": "fake-chat",
        "requires_legal_footer": False,
    }
    fields.update(overrides)
    return template_service.save_template(MessageTemplate(channel=channel, **fields))


def make_static(channel: Channel, **overrides) -> MessageTemplate:
    fields = {"key": "followup", "kind": TemplateKind.STATIC, "subject": "Hi {first_name}", "body": "Following up."}
    fields.update({"json_schema": None, "model": ""}, **overrides)
    return make_template(channel, **fields)
