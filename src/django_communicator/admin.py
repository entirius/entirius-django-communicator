# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django.contrib import admin

from django_communicator.models import Channel, Message, MessageTemplate, MessageTemplateVersion, Suppression
from django_communicator.services import template_service


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("idx", "label", "mode", "live_enabled", "default_language")
    search_fields = ("idx", "label")
    filter_horizontal = ("languages",)


class MessageTemplateVersionInline(admin.TabularInline):
    model = MessageTemplateVersion
    fk_name = "template"
    extra = 0
    can_delete = False
    fields = ("number", "subject", "model", "created_by", "created_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(MessageTemplate)
class MessageTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "channel", "language", "kind", "auto_approve", "is_active", "current_version")
    list_filter = ("channel", "kind", "is_active")
    search_fields = ("key", "subject")
    readonly_fields = ("current_version",)
    inlines = [MessageTemplateVersionInline]

    def save_model(self, request, obj, form, change) -> None:
        """Every content change goes through the versioning service."""
        template_service.save_template(obj, user=request.user)


@admin.register(Suppression)
class SuppressionAdmin(admin.ModelAdmin):
    list_display = ("value", "kind", "channel", "reason", "created_at")
    list_filter = ("channel", "kind")
    search_fields = ("value",)


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "status", "version", "failure_code", "created_at")
    list_filter = ("status", "failure_code")
    search_fields = ("subject", "thread__subject_ref", "thread__recipient_email")
    list_select_related = ("thread",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
