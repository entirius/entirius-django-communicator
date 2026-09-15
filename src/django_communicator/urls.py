# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Root URL config for django_communicator — mounts the Admin API v2 namespace."""

from django.urls import include, path

urlpatterns = [path("api/communicator/v2/admin/<str:channel_idx>/", include("django_communicator.api.admin.urls"))]
