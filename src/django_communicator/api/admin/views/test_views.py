# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Development-only endpoints for BDD: communicate(), channel clock, beat run, sequence start, IMAP poll, counters.

404 outside `ENVIRONMENT == "development"`.
"""

from collections.abc import Callable
from typing import TypeVar

from celery_once import AlreadyQueued
from django.db import transaction
from django.utils.decorators import method_decorator
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response

from django_communicator.api.admin.views._base import ERROR_RESPONSES, Conflict, DevelopmentView, parse
from django_communicator.models import Sequence, Thread
from django_communicator.schemas.requests import (
    DevClockRequest,
    DevCommunicateRequest,
    DevResetCountersRequest,
    DevStartSequenceRequest,
)
from django_communicator.schemas.responses import (
    ClockResponse,
    CountersResetResponse,
    MessageDetailResponse,
    PollNowResponse,
    SendDueResponse,
    SequenceStateResponse,
)
from django_communicator.services import clock_service, counter_service, poll_service, sequence_service
from django_communicator.services.communicate_service import LegalFooterRequiredError, communicate
from django_communicator.tasks import poll_inbox, schedule_follow_ups, send_due

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
        description="Returns the created message; a missing legal footer answers 400 and creates nothing.",
        request=DevCommunicateRequest,
        responses={201: MessageDetailResponse, **ERROR_RESPONSES},
    )
    def post(self, request: Request, channel_idx: str) -> Response:
        body = parse(DevCommunicateRequest, request.data)
        channel = self.channel(channel_idx)
        try:
            message = communicate(channel_idx=channel.idx, **dict(body))
        except LegalFooterRequiredError as error:
            raise ValidationError({"recipient.legal_footer": [str(error)]}) from None
        return Response(MessageDetailResponse.of(message).model_dump(mode="json"), status=201)


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
            raise ValidationError({"sequence_key": [str(error)]}) from None
        return Response(SequenceStateResponse.model_validate(state).model_dump(mode="json"), status=201)


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
