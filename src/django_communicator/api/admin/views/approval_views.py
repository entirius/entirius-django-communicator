# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API v2 — the approval inbox across channels (outbound Q4): filtered list, bulk approve / reject, log.

Mounted outside `<channel_idx>/`: `api/communicator/v2/admin/approvals/`.
"""

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, AdminPagination, AdminView, parse
from django_communicator.models import BulkApprovalLog, Message
from django_communicator.schemas.requests import ApprovalQuery, BulkApprovalRequest
from django_communicator.schemas.responses import (
    ApprovalCountsResponse,
    ApprovalListResponse,
    ApprovalRowResponse,
    BulkApprovalLogListResponse,
    BulkApprovalLogResponse,
    BulkApprovalResponse,
)
from django_communicator.services import approval_service, preflight_service, sender_route_service

_TAGS = ["Communicator Approvals"]
PREVIEW_CHARS = 300


def _row(message: Message, routes: dict, now) -> ApprovalRowResponse:
    thread, version = message.thread, message.template_version
    try:
        route = sender_route_service.route_for(message, routes)
    except sender_route_service.SenderRouteError:
        route = None
    found = preflight_service.problem(message, routes)
    return ApprovalRowResponse(
        id=message.pk,
        channel=thread.channel.idx,
        thread_id=thread.pk,
        sender_route=sender_route_service.route_key(message),
        sender_from=route.from_email if route else "",
        sender_cc=route.cc_email if route else "",
        company=str(message.render_context.get("company_name") or thread.recipient_name),
        subject_ref=thread.subject_ref,
        recipient_email=thread.recipient_email,
        template_key=version.template.key if version else "",
        template_version=version.number if version else None,
        subject=message.subject,
        body_preview=message.body_text[:PREVIEW_CHARS],
        version=message.version,
        created_at=message.created_at,
        age_hours=round((now - message.created_at).total_seconds() / 3600, 2),
        blocker=found[1] if found else "",
    )


class ApprovalListView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        operation_id="communicator_approvals_list",
        summary="Every draft waiting for approval, all channels",
        description="Oldest first, paginated (`page`, `page_size` ≤ 100). Filters: `channel`, `sender_route` (effective "
        "route key), `template` (template key), `q` (subject reference, recipient or subject), `min_age_hours`. "
        "`counts` covers the whole filtered set. `blocker` says why approving a row would be refused.",
        parameters=[
            OpenApiParameter("channel", str),
            OpenApiParameter("sender_route", str),
            OpenApiParameter("template", str),
            OpenApiParameter("q", str),
            OpenApiParameter("min_age_hours", float),
            OpenApiParameter("page", int),
            OpenApiParameter("page_size", int),
        ],
        responses={200: ApprovalListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, **kwargs) -> Response:
        query = parse(ApprovalQuery, request.query_params.dict())
        now = timezone.now()
        rows = approval_service.pending(
            channel_idx=query.channel,
            sender_route=query.sender_route,
            template_key=query.template,
            search=query.q,
            min_age_hours=query.min_age_hours,
            now=now,
        )
        paginator = AdminPagination()
        page = list(paginator.paginate_queryset(rows, request, view=self))
        routes = approval_service.route_map(page)
        grouped = approval_service.counts(rows)
        payload = ApprovalListResponse(
            count=paginator.page.paginator.count,
            next=paginator.get_next_link(),
            previous=paginator.get_previous_link(),
            counts=ApprovalCountsResponse(total=paginator.page.paginator.count, **grouped),
            results=[_row(message, routes, now) for message in page],
        )
        return Response(payload.model_dump(mode="json"))


class BulkApprovalView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        summary="Approve or reject several drafts at once",
        description="Each id is handled on its own: a draft that is no longer waiting, is unknown or would be refused "
        "(legal footer, sender route) lands in `failed` with the reason and never blocks the others. At most "
        "`COMMUNICATOR_BULK_APPROVAL_MAX` ids (default 100), else 400. The batch is logged with the user.",
        request=BulkApprovalRequest,
        responses={200: BulkApprovalResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, **kwargs) -> Response:
        body = parse(BulkApprovalRequest, request.data)
        try:
            log = approval_service.bulk(user=request.user, action=body.action, ids=body.ids, reason=body.reason)
        except approval_service.BulkLimitError as error:
            raise ValidationError({"ids": [error.client_message]}) from None
        payload = BulkApprovalResponse(id=log.pk, action=log.action, done=log.done_ids, failed=log.failed)
        return Response(payload.model_dump(mode="json"))


class BulkApprovalLogView(AdminView):
    access_area = "communicator.review"

    @extend_schema(
        tags=_TAGS,
        summary="Log of bulk approvals and rejections",
        description="Newest first, paginated: who, what, which ids were changed and which were not.",
        responses={200: BulkApprovalLogListResponse, **ERROR_RESPONSES},
    )
    def get(self, request: Request, **kwargs) -> Response:
        paginator = AdminPagination()
        page = paginator.paginate_queryset(BulkApprovalLog.objects.order_by("-created_at", "-pk"), request, view=self)
        results = [
            BulkApprovalLogResponse(
                id=log.pk,
                user_id=log.user_id,
                action=log.action,
                reason=log.reason,
                requested=len(log.requested_ids),
                done_ids=log.done_ids,
                failed=log.failed,
                created_at=log.created_at,
            )
            for log in page
        ]
        return paginator.get_paginated_response([r.model_dump(mode="json") for r in results])
