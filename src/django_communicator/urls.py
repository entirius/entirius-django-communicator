# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Root URL config for django_communicator — mounts the Admin API v2 namespace, the cross-channel approval inbox
and the public unsubscribe endpoint."""

from django.urls import include, path

from django_communicator.api.admin.views import approval_views as approval
from django_communicator.api.public.views import GmailCallbackView, UnsubscribeView

# The approval inbox is listed before the channel-scoped include, so a channel idx can never shadow it.
urlpatterns = [
    path(
        "api/communicator/v2/admin/approvals/", approval.ApprovalListView.as_view(), name="admin-communicator-approvals"
    ),
    path(
        "api/communicator/v2/admin/approvals/bulk/",
        approval.BulkApprovalView.as_view(),
        name="admin-communicator-approvals-bulk",
    ),
    path(
        "api/communicator/v2/admin/approvals/log/",
        approval.BulkApprovalLogView.as_view(),
        name="admin-communicator-approvals-log",
    ),
    path(
        "api/communicator/v2/public/unsubscribe/<str:token>/",
        UnsubscribeView.as_view(),
        name="communicator-unsubscribe",
    ),
    path("api/communicator/v2/public/gmail/callback/", GmailCallbackView.as_view(), name="communicator-gmail-callback"),
    path("api/communicator/v2/admin/<str:channel_idx>/", include("django_communicator.api.admin.urls")),
]
