# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.urls import path

from django_communicator.api.admin.views import mailbox_views as mailbox
from django_communicator.api.admin.views import reply_views as reply
from django_communicator.api.admin.views import review_views as review
from django_communicator.api.admin.views import sending_views as sending
from django_communicator.api.admin.views import sequence_views as sequence
from django_communicator.api.admin.views import suppression_views as suppression
from django_communicator.api.admin.views import template_views as template
from django_communicator.api.admin.views import test_views as dev
from django_communicator.api.admin.views import thread_views as thread
from django_communicator.api.admin.views._base import is_development

urlpatterns = [
    path("review/", review.ReviewListView.as_view(), name="admin-communicator-review-list"),
    path("review/next/", review.ReviewNextView.as_view(), name="admin-communicator-review-next"),
    path("review/<int:pk>/", review.ReviewDetailView.as_view(), name="admin-communicator-review-detail"),
    path("review/<int:pk>/accept/", review.AcceptView.as_view(), name="admin-communicator-review-accept"),
    path("review/<int:pk>/rewrite/", review.RewriteView.as_view(), name="admin-communicator-review-rewrite"),
    path("review/<int:pk>/edit/", review.EditView.as_view(), name="admin-communicator-review-edit"),
    path("review/<int:pk>/skip/", review.SkipView.as_view(), name="admin-communicator-review-skip"),
    path(
        "review/<int:pk>/skip-company/", review.SkipCompanyView.as_view(), name="admin-communicator-review-skip-company"
    ),
    path("templates/", template.TemplateListView.as_view(), name="admin-communicator-templates"),
    path("templates/<int:pk>/", template.TemplateDetailView.as_view(), name="admin-communicator-template"),
    path(
        "templates/<int:pk>/versions/",
        template.TemplateVersionsView.as_view(),
        name="admin-communicator-template-versions",
    ),
    path(
        "templates/<int:pk>/test-generate/",
        template.TemplateTestGenerateView.as_view(),
        name="admin-communicator-template-test-generate",
    ),
    path("models/", template.ModelListView.as_view(), name="admin-communicator-models"),
    path("suppressions/", suppression.SuppressionListView.as_view(), name="admin-communicator-suppressions"),
    path("suppressions/<int:pk>/", suppression.SuppressionDetailView.as_view(), name="admin-communicator-suppression"),
    path("channel/", sending.ChannelConfigView.as_view(), name="admin-communicator-channel"),
    path("policy/", sending.PolicyView.as_view(), name="admin-communicator-policy"),
    path("messages/", sending.OutboxListView.as_view(), name="admin-communicator-messages"),
    path("messages/<int:pk>/send-now/", sending.SendNowView.as_view(), name="admin-communicator-send-now"),
    path("sequences/", sequence.SequenceListView.as_view(), name="admin-communicator-sequences"),
    path(
        "sequences/<int:pk>/steps/", sequence.SequenceStepListView.as_view(), name="admin-communicator-sequence-steps"
    ),
    path("sequences/<int:pk>/texts/", sequence.TextListView.as_view(), name="admin-communicator-sequence-texts"),
    path("threads/", thread.ThreadListView.as_view(), name="admin-communicator-threads"),
    path("threads/<int:pk>/", thread.ThreadDetailView.as_view(), name="admin-communicator-thread"),
    path(
        "threads/<int:pk>/resume-sequence/",
        thread.ResumeSequenceView.as_view(),
        name="admin-communicator-thread-resume-sequence",
    ),
    path("replies/", reply.ReplyListView.as_view(), name="admin-communicator-replies"),
    path(
        "replies/<int:pk>/confirm-optout/",
        reply.ConfirmOptoutView.as_view(),
        name="admin-communicator-reply-confirm-optout",
    ),
    path(
        "replies/<int:pk>/dismiss-optout/",
        reply.DismissOptoutView.as_view(),
        name="admin-communicator-reply-dismiss-optout",
    ),
    path("mailbox/", mailbox.MailboxView.as_view(), name="admin-communicator-mailbox"),
]

if is_development():
    urlpatterns += [
        path("test/communicate/", dev.DevCommunicateView.as_view(), name="admin-communicator-test-communicate"),
        path("test/clock/", dev.DevClockView.as_view(), name="admin-communicator-test-clock"),
        path("test/send-due/", dev.DevSendDueView.as_view(), name="admin-communicator-test-send-due"),
        path("test/reset-counters/", dev.DevResetCountersView.as_view(), name="admin-communicator-test-reset-counters"),
        path("test/start-sequence/", dev.DevStartSequenceView.as_view(), name="admin-communicator-test-start-sequence"),
        path("test/poll-now/", dev.DevPollNowView.as_view(), name="admin-communicator-test-poll-now"),
    ]
