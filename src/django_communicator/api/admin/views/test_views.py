# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Development-only endpoints for BDD: communicate(), compose(), channel clock, beat run, sequence start, IMAP poll, counters,
draft retry run and the toolbox outage switch.

404 outside `ENVIRONMENT == "development"`.
"""

from collections.abc import Callable
from typing import TypeVar

from celery_once import AlreadyQueued
from django.db import transaction
from django.utils.decorators import method_decorator
from django_utils.toolbox import ToolboxDisabledError, handle_toolbox_error, outage
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, Conflict, DevelopmentView, parse
from django_communicator.models import Reply, Sequence, Thread
from django_communicator.schemas.requests import (
    DevClockRequest,
    DevCommunicateRequest,
    DevComposeRequest,
    DevIngestReplyRequest,
    DevResetCountersRequest,
    DevStartSequenceRequest,
    DevToolboxOutageRequest,
)
from django_communicator.schemas.responses import (
    ClockResponse,
    CountersResetResponse,
    MessageDetailResponse,
    PollNowResponse,
    ReplyResponse,
    RetryDraftsResponse,
    SendDueResponse,
    SequenceStateResponse,
    ToolboxOutageResponse,
)
from django_communicator.services import (
    clock_service,
    counter_service,
    draft_retry_service,
    inbound_service,
    poll_service,
    sequence_service,
)
from django_communicator.services.communicate_service import LegalFooterRequiredError, ThreadMismatchError, communicate
from django_communicator.services.compose_service import ComposeError, compose
from django_communicator.services.reply_class_service import Classification
from django_communicator.tasks import poll_inbox, retry_failed_drafts, schedule_follow_ups, send_due

_TAGS = ["Communicator (development)"]
T = TypeVar("T")


def _run_locked(task, body: Callable[[], T]) -> T:
    """`body` under the celery-once lock of the beat `task`; 409 while a beat run holds it."""
    key = task.get_key((), {})
    try:
        task.once_backend.raise_or_lock(key, timeout=task.once.get("timeout", task.default_timeout))
    except AlreadyQueued:
        raise Conflict(f"{task.name.rpartition('.')[2]} is already running", code="already_running") from None
    try:
        return body()
    finally:
        task.once_backend.clear_lock(key)


class DevCommunicateView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Call communicate() (development only)",
        description="Returns the created message; a missing legal footer answers 400 and creates nothing. "
        "503 `AI_TOOLBOX_DISABLED` for an AI template while the toolbox is switched off.",
        request=DevCommunicateRequest,
        responses={201: MessageDetailResponse, **ERROR_RESPONSES, 503: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevCommunicateRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            message = communicate(channel_idx=channel.idx, **dict(body))
        except LegalFooterRequiredError as error:
            raise ValidationError({"recipient.legal_footer": [error.client_message]}) from None
        except ToolboxDisabledError as error:
            return handle_toolbox_error(error)
        return Response(MessageDetailResponse.of(message).model_dump(mode="json"), status=201)


class DevComposeView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Call compose() (development only)",
        description="A hand-written message by the requesting user: `approved` and due now, or `suppressed`. "
        "A missing legal footer, a blank or too long subject or body, or a thread or reply of another subject answers "
        "400 and creates nothing.",
        request=DevComposeRequest,
        responses={201: MessageDetailResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevComposeRequest, request.data)
        channel = self.channel(channel_idx)
        fields = body.model_dump(exclude={"thread_id", "in_reply_to_id", "recipient"})
        thread = _channel_object(Thread, channel, body.thread_id)
        reply = _channel_object(Reply, channel, body.in_reply_to_id)
        try:
            message = compose(
                channel_idx=channel.idx,
                recipient=body.recipient,
                thread=thread,
                in_reply_to=reply,
                author=request.user,
                **fields,
            )
        except LegalFooterRequiredError as error:
            raise ValidationError({"recipient.legal_footer": [error.client_message]}) from None
        except (ComposeError, ThreadMismatchError) as error:
            raise ValidationError({"non_field_errors": [str(error)]}) from None
        return Response(MessageDetailResponse.of(message).model_dump(mode="json"), status=201)


def _channel_object(model, channel, pk: int | None):
    """The thread or reply `pk` of this channel; None for no pk; 404 for another channel's."""
    if pk is None:
        return None
    found = model.objects.filter(channel=channel, pk=pk).first()
    if found is None:
        raise NotFound(f"{model.__name__} not found.")
    return found


class DevClockView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Move the channel clock (development only)",
        request=DevClockRequest,
        responses={200: ClockResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevClockRequest, request.data)
        channel = self.channel(channel_idx)
        clock_service.set_override(channel, body.iso_datetime)
        return Response(ClockResponse(now=clock_service.now_for(channel)).model_dump(mode="json"))


@method_decorator(transaction.non_atomic_requests, name="dispatch")  # the send-once claim must commit on its own
class DevSendDueView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Run schedule_follow_ups then send_due (development only)",
        description="Runs both task bodies in-process over every channel under the send_due celery-once lock of the "
        "beat — 409 while a beat run holds it; the counts are totals.",
        request=None,
        responses={200: SendDueResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        follow_ups = schedule_follow_ups.run()
        counts = _run_locked(send_due, send_due.run)
        return Response(SendDueResponse(follow_ups_scheduled=follow_ups, **counts).model_dump(mode="json"))


class DevResetCountersView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Clear the channel's daily send counters (development only)",
        request=DevResetCountersRequest,
        responses={200: CountersResetResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevResetCountersRequest, request.data)
        channel = self.channel(channel_idx)
        for day in body.days:
            counter_service.reset(channel, day)
        return Response(CountersResetResponse(cleared=len(body.days)).model_dump(mode="json"))


class DevStartSequenceView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Start a sequence in a thread (development only)",
        request=DevStartSequenceRequest,
        responses={201: SequenceStateResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevStartSequenceRequest, request.data)
        channel = self.channel(channel_idx)
        thread = Thread.objects.filter(channel=channel, pk=body.thread_id).first()
        sequence = Sequence.objects.filter(channel=channel, key=body.sequence_key).first()
        if thread is None or sequence is None:
            raise NotFound("Thread or sequence not found.")
        try:
            state = sequence_service.start_sequence(thread, sequence)
        except sequence_service.SequenceError as error:
            raise ValidationError({"sequence_key": [error.client_message]}) from None
        return Response(SequenceStateResponse.model_validate(state).model_dump(mode="json"), status=201)


class DevIngestReplyView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Ingest a human reply with a given class (development only)",
        description="Stores a reply to the thread through the normal write path (opt-out detection, plan of the class, "
        "alert, signal) with this class instead of a toolbox call; no `intent` = an unclassified reply.",
        request=DevIngestReplyRequest,
        responses={201: ReplyResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevIngestReplyRequest, request.data)
        channel = self.channel(channel_idx)
        thread = Thread.objects.select_related("channel", "recipient_language").filter(channel=channel)
        thread = thread.filter(pk=body.thread_id).first()
        if thread is None:
            raise NotFound("Thread not found.")
        given = Classification(body.intent, body.confidence, body.return_date) if body.intent else None
        reply = inbound_service.ingest_with_class(thread, body.body, given)
        return Response(ReplyResponse.model_validate(reply).model_dump(mode="json"), status=201)


class DevPollNowView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Run the IMAP poll now (development only)",
        description="Polls the active mailbox of this channel in-process under the poll_inbox celery-once lock of the "
        "beat — 409 while a beat run holds it.",
        request=None,
        responses={200: PollNowResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        channel = self.channel(channel_idx)
        counts = _run_locked(poll_inbox, lambda: poll_service.poll_all(channel))
        return Response(PollNowResponse(**counts).model_dump(mode="json"))


@method_decorator(transaction.non_atomic_requests, name="dispatch")  # no toolbox call inside a transaction
class DevRetryDraftsView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Run retry_failed_drafts now (development only)",
        description="Runs the beat task body in-process over every channel under its celery-once lock — 409 while a "
        "beat run holds it. Nothing is retried while the toolbox status is not `configured`.",
        request=None,
        responses={200: RetryDraftsResponse, **ERROR_RESPONSES, 409: None},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        self.channel(channel_idx)
        counts = _run_locked(retry_failed_drafts, draft_retry_service.retry_failed_drafts)
        return Response(RetryDraftsResponse(**counts).model_dump(mode="json"))


class DevToolboxOutageView(DevelopmentView):
    @extend_schema(
        tags=_TAGS,
        summary="Simulate an AI toolbox outage (development only)",
        description="Turns the `django_utils.toolbox.outage` switch on or off for every process sharing the cache. "
        "404 where the switch is not allowed (no DEBUG and no AI_TOOLBOX_TEST_SWITCH, or production).",
        request=DevToolboxOutageRequest,
        responses={200: ToolboxOutageResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevToolboxOutageRequest, request.data)
        self.channel(channel_idx)
        if not outage.allowed():
            raise NotFound()
        outage.set_down(body.down)
        return Response(ToolboxOutageResponse(down=outage.is_down()).model_dump(mode="json"))
