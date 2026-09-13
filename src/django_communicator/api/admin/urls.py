# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.urls import path

from django_communicator.api.admin.views import review_views as review
from django_communicator.api.admin.views import suppression_views as suppression
from django_communicator.api.admin.views import template_views as template
from django_communicator.api.admin.views._base import is_development
from django_communicator.api.admin.views.test_views import DevCommunicateView

urlpatterns = [
    path("review/", review.ReviewListView.as_view(), name="admin-communicator-review-list"),
    path("review/next/", review.ReviewNextView.as_view(), name="admin-communicator-review-next"),
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
]

if is_development():
    urlpatterns += [path("test/communicate/", DevCommunicateView.as_view(), name="admin-communicator-test-communicate")]
