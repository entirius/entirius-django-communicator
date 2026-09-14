# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django import forms
from django.contrib import admin

from django_communicator.models import (
    Channel,
    InboundQuarantine,
    MailboxConfig,
    Message,
    MessageTemplate,
    MessageTemplateVersion,
    Reply,
    SendPolicy,
    SendWindow,
    Sequence,
    SequenceStep,
    Suppression,
    TextPool,
    ThreadSequenceState,
)
from django_communicator.services import inbox_service, template_service


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

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class SendWindowInline(admin.TabularInline):
    model = SendWindow
    extra = 0


@admin.register(SendPolicy)
class SendPolicyAdmin(admin.ModelAdmin):
    list_display = ("channel", "business_days_only", "daily_cap", "spread")
    inlines = [SendWindowInline]


class SequenceStepInline(admin.TabularInline):
    model = SequenceStep
    extra = 0


class TextPoolInline(admin.TabularInline):
    model = TextPool
    extra = 0


@admin.register(Sequence)
class SequenceAdmin(admin.ModelAdmin):
    list_display = ("key", "channel", "is_active")
    list_filter = ("channel", "is_active")
    inlines = [SequenceStepInline, TextPoolInline]


@admin.register(ThreadSequenceState)
class ThreadSequenceStateAdmin(admin.ModelAdmin):
    list_display = ("thread", "sequence", "step", "next_due_at", "stopped_at", "stop_reason")
    list_filter = ("stop_reason",)
    list_select_related = ("thread", "sequence")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


class MailboxConfigForm(forms.ModelForm):
    imap_password = forms.CharField(
        widget=forms.PasswordInput(render_value=False), required=False, help_text="Leave empty to keep the stored one."
    )

    class Meta:
        model = MailboxConfig
        fields = ("channel", "imap_host", "imap_port", "imap_use_ssl", "imap_user", "imap_password", "folder")
        fields += ("last_uid", "is_active")

    def clean_imap_password(self) -> str:
        return self.cleaned_data["imap_password"] or self.instance.imap_password


@admin.register(MailboxConfig)
class MailboxConfigAdmin(admin.ModelAdmin):
    form = MailboxConfigForm
    list_display = ("channel", "imap_host", "imap_user", "folder", "is_active", "last_uid", "last_polled_at")
    readonly_fields = ("last_polled_at",)

    def save_model(self, request, obj, form, change) -> None:
        """Another host, user or folder is another mailbox: the cursor starts over (as `inbox_service.save_mailbox`)."""
        if change and set(form.changed_data) & set(inbox_service.MAILBOX_IDENTITY):
            obj.last_uid, obj.uid_validity = 0, None
        super().save_model(request, obj, form, change)


@admin.register(InboundQuarantine)
class InboundQuarantineAdmin(admin.ModelAdmin):
    list_display = ("mailbox", "uid", "reason", "size", "received_at")
    list_filter = ("reason",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Reply)
class ReplyAdmin(admin.ModelAdmin):
    list_display = ("from_email", "subject", "kind", "matched_by", "thread", "received_at")
    list_filter = ("kind",)
    list_select_related = ("thread",)
    search_fields = ("from_email", "subject")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False
