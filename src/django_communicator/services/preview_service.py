# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Template editor preview: render a template with a sample context and draft it without saving anything."""

from typing import Any

from django_communicator.enums import TemplateKind
from django_communicator.models import MessageTemplate
from django_communicator.services import drafting_service
from django_communicator.services.render_service import render

TEST_GENERATE_TAG = "communicator.test_generate"


def generate_preview(template: MessageTemplate, context: dict[str, Any]) -> dict[str, Any]:
    """Raises `RenderError`, `ToolboxError` or `DraftOutputError`; static templates never call the toolbox."""
    version = template.current_version
    if template.kind == TemplateKind.STATIC:
        subject, body_text = render(version.subject, context), render(version.body, context)
        return {"subject": subject, "body_text": body_text, "rendered_prompt": "", "model": "", "usage": {}}
    prompt = render(version.body, context)
    draft = drafting_service.generate(
        prompt=prompt, version=version, tag=TEST_GENERATE_TAG, channel_idx=template.channel.idx
    )
    fields = {"subject": draft.subject, "body_text": draft.body_text, "model": draft.model, "usage": draft.usage}
    return {**fields, "rendered_prompt": prompt}
