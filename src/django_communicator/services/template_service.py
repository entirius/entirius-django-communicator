# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Template resolution (recipient language → channel default → none) and content versioning on save."""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, QuerySet
from django_regional.models import Language

from django_communicator.models import Channel, MessageTemplate, MessageTemplateVersion

CONTENT_FIELDS = ("subject", "body", "json_schema", "model")
UPDATABLE_FIELDS = ("key", "kind", *CONTENT_FIELDS, "requires_legal_footer", "auto_approve", "is_active")


class NoTemplateError(Exception):
    pass


def find_language(code: str) -> Language | None:
    return Language.objects.filter(iso2__iexact=code.strip()).first() if code else None


def resolve(channel: Channel, key: str, language_code: str) -> MessageTemplate:
    """Active template in the recipient's language, else the channel default; never guessed."""
    templates = MessageTemplate.objects.select_related("current_version").filter(
        channel=channel, key=key, is_active=True, current_version__isnull=False
    )
    language = find_language(language_code)
    for language_id in (language.pk if language else None, channel.default_language_id):
        template = templates.filter(language_id=language_id).first() if language_id else None
        if template:
            return template
    raise NoTemplateError(f"no active template {key!r} for language {language_code!r} or the channel default")


def list_templates(channel: Channel) -> QuerySet[MessageTemplate]:
    return MessageTemplate.objects.select_related("language", "current_version").filter(channel=channel).order_by("key")


def get_template(channel: Channel, pk: int) -> MessageTemplate:
    """Raises `MessageTemplate.DoesNotExist` when the template is not in this channel."""
    return list_templates(channel).get(pk=pk)


def list_versions(template: MessageTemplate) -> QuerySet[MessageTemplateVersion]:
    return template.versions.all()


def update_template(template: MessageTemplate, *, language_code: str, user=None, **fields) -> MessageTemplate:
    """Apply the whitelisted content fields and save with versioning; raises `ValidationError` (unknown language too)."""
    language = find_language(language_code)
    if language is None:
        raise ValidationError({"language": ["Unknown language code."]})
    for name in UPDATABLE_FIELDS:
        if name in fields:
            setattr(template, name, fields[name])
    template.language = language
    return save_template(template, user=user)


@transaction.atomic
def save_template(template: MessageTemplate, *, user=None) -> MessageTemplate:
    """Validate and save; a content change (or a first save) creates the next `MessageTemplateVersion`.

    Raises `django.core.exceptions.ValidationError` on invalid content or a duplicate (channel, key, language).
    """
    template.full_clean(exclude=["current_version"])
    template.save()
    current = template.current_version
    if current and _content(current) == _content(template):
        return template
    number = (template.versions.aggregate(last=Max("number"))["last"] or 0) + 1
    version = MessageTemplateVersion.objects.create(
        template=template, number=number, created_by=user, **_content(template)
    )
    template.current_version = version
    template.save(update_fields=["current_version", "modified_at"])
    return template


def _content(obj: MessageTemplate | MessageTemplateVersion) -> dict:
    return {field: getattr(obj, field) for field in CONTENT_FIELDS}
