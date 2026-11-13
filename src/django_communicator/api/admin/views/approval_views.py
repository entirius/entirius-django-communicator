# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — the approval inbox across channels. Mounted before the `<channel_idx>/` include, at
`api/communicator/v2/admin/approvals/`."""

from datetime import datetime

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, parse
from django_communicator.models import Message
from django_communicator.schemas.requests import ApprovalQuery, BulkApprovalRequest
from django_communicator.schemas.responses import (
    ApprovalCountsResponse,
    ApprovalRowResponse,
    BulkApprovalLogListResponse,
    BulkApprovalLogResponse,
    BulkApprovalResponse,
)
from django_communicator.schemas.responses import ApprovalListResponse as ListResponse
from django_communicator.services import approval_service, preflight_service

_TAGS = ["Communicator Approvals"]
PREVIEW_CHARS = 300


def _row(message: Message, now: datetime) -> dict:
    thread, version = message.thread, message.template_version
    blocker = preflight_service.problem(message)
    return ApprovalRowResponse(
        id=message.pk,
        channel=thread.channel.idx,
        thread_id=thread.pk,
        subject_ref=thread.subject_ref,
        recipient_email=thread.recipient_email,
        recipient_name=thread.recipient_name,
        template_key=version.template.key if version else "",
        subject=message.subject,
        body_preview=message.body_text[:PREVIEW_CHARS],
        created_at=message.created_at,
        age_hours=round((now - message.created_at).total_seconds() / 3600, 2),
        blocker=blocker[1] if blocker else None,
    ).model_dump(mode="json")


class ApprovalListView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_approvals_list",
        summary="Every outbound draft waiting for approval, all channels",
        description="Oldest first, paginated (`page`, `page_size` ≤ 100). `counts` covers the whole filtered set. "
        "`blocker` says why approving a row would be refused (null: it may be approved): a missing legal footer or a "
        "mailbox that is paused, disabled or needs a login.",
        parameters=[
            OpenApiParameter("channel", str),
            OpenApiParameter("template_key", str),
            OpenApiParameter("search", str),
            OpenApiParameter("min_age_hours", float),
            OpenApiParameter("mailbox", int),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request) -> Response:
        query = parse(ApprovalQuery, request.query_params.dict())
        filters = approval_service.Filters(
            query.channel, query.template_key, query.search, query.min_age_hours, query.mailbox
        )
        now = timezone.now()
        rows = approval_service.pending(filters, now)
        paginator = AdminPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        response = paginator.get_paginated_response([_row(message, now) for message in page])
        counts = ApprovalCountsResponse(
            total=paginator.page.paginator.count,
            by_channel=approval_service.counts_by_channel(rows),
            by_mailbox=approval_service.counts_by_mailbox(rows),
        )
        response.data["counts"] = counts.model_dump()
        return response


class BulkApprovalView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        summary="Approve or reject several drafts at once",
        description="Each id is handled on its own: a draft that is unknown, no longer waiting or refused by the "
        "preflight (legal footer) lands in `failed` with the reason and never blocks the others. At most "
        "`COMMUNICATOR_BULK_APPROVAL_MAX` ids (default 100), else 400. Every call is logged with the user.",
        request=BulkApprovalRequest,
        responses={200: BulkApprovalResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request) -> Response:
        body = parse(BulkApprovalRequest, request.data)
        try:
            log = approval_service.bulk(user=request.user, action=body.action, ids=body.ids, reason=body.reason)
        except approval_service.BulkLimitError as error:
            raise ValidationError({"ids": [error.client_message]}) from None
        payload = BulkApprovalResponse(id=log.pk, action=log.action, done=log.done, failed=log.failed)
        return Response(payload.model_dump(mode="json"))


class BulkApprovalLogView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_approvals_log_list",
        summary="Log of bulk approvals and rejections",
        description="Newest first, paginated: who, what, which ids were changed and which were not.",
        parameters=[OpenApiParameter("page", int), OpenApiParameter("page_size", int)],
        responses={200: BulkApprovalLogListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request) -> Response:
        paginator = AdminPagination()
        page = paginator.paginate_queryset(approval_service.list_logs(), request, view=self)
        results = [BulkApprovalLogResponse.model_validate(log).model_dump(mode="json") for log in page]
        return paginator.get_paginated_response(results)
