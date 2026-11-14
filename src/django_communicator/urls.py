# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Root URL config for django_communicator — the public unsubscribe route, the cross-channel approval inbox and the
channel-scoped Admin API v2 namespace."""

from django.urls import include, path

from django_communicator.api.admin.views import approval_views as approval
from django_communicator.api.public.views import UnsubscribeView

APPROVALS = "approvals"

# The approval routes come before the `<channel_idx>/` include, so a channel can never shadow them; the system check
# `communicator.reserved_idx` refuses a channel whose idx equals APPROVALS.
urlpatterns = [
    path(
        f"api/communicator/v2/admin/{APPROVALS}/",
        approval.ApprovalListView.as_view(),
        name="admin-communicator-approvals",
    ),
    path(
        f"api/communicator/v2/admin/{APPROVALS}/bulk/",
        approval.BulkApprovalView.as_view(),
        name="admin-communicator-approvals-bulk",
    ),
    path(
        f"api/communicator/v2/admin/{APPROVALS}/log/",
        approval.BulkApprovalLogView.as_view(),
        name="admin-communicator-approvals-log",
    ),
    path(
        "api/communicator/v2/public/unsubscribe/<str:token>/",
        UnsubscribeView.as_view(),
        name="communicator-unsubscribe",
    ),
    path("api/communicator/v2/admin/<str:channel_idx>/", include("django_communicator.api.admin.urls")),
]
