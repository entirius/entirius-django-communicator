# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
from django import forms
from django.contrib import admin

from django_communicator.models import (
    Channel,
    HealthPolicy,
    InboundQuarantine,
    Mailbox,
    MailboxPauseLog,
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
from django_communicator.services import mailbox_service, template_service


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


class MailboxForm(forms.ModelForm):
    smtp_password = forms.CharField(
        widget=forms.PasswordInput(render_value=False), required=False, help_text="Leave empty to keep the stored one."
    )
    imap_password = forms.CharField(
        widget=forms.PasswordInput(render_value=False), required=False, help_text="Leave empty to keep the stored one."
    )

    class Meta:
        model = Mailbox
        fields = ("channel", "address", "display_name", "cc_address", "user", "is_default", "auth_method")
        fields += ("smtp_host", "smtp_port", "smtp_security", "smtp_user", "smtp_password", "send_enabled")
        fields += ("imap_host", "imap_port", "imap_use_ssl", "imap_user", "imap_password", "folder")
        fields += ("receive_enabled", "daily_cap", "warmup_start_cap", "warmup_step", "warmup_started_on")

    def mailbox_changes(self) -> dict:
        """What the mailbox service writes: the changed fields; `user` as `user_id`; `channel` only on create."""
        changes = {name: self.cleaned_data[name] for name in self.changed_data if name not in ("channel", "user")}
        return changes | ({"user_id": self.instance.user_id} if "user" in self.changed_data else {})

    def clean(self) -> dict:
        """The service's refusals (warm-up without a ceiling, a non-staff user, a non-ASCII password) as form errors."""
        cleaned = super().clean()
        if self.errors:
            return cleaned
        try:
            mailbox_service.check_changes(self._original(), self.mailbox_changes())
        except mailbox_service.MailboxError as error:
            raise forms.ValidationError(error.client_message) from None
        return cleaned

    def _original(self) -> Mailbox:
        if self.instance.pk:
            return Mailbox.objects.get(pk=self.instance.pk)
        return Mailbox(channel=self.cleaned_data.get("channel"))

    def _get_validation_exclusions(self):
        """The service moves the default flag from the old default; the one-default constraint must not refuse it."""
        return {*super()._get_validation_exclusions(), "is_default"}


@admin.register(Mailbox)
class MailboxAdmin(admin.ModelAdmin):
    """Passwords are written only when typed in; pause and resume belong to the API (they are logged)."""

    form = MailboxForm
    list_display = ("address", "channel", "user", "is_default", "send_enabled", "receive_enabled", "paused_at")
    list_filter = ("send_enabled", "receive_enabled", "is_default")
    search_fields = ("address", "display_name")
    readonly_fields = ("smtp_login_failed_at", "paused_at", "pause_reason", "health_reset_at", "last_polled_at")

    def save_model(self, request, obj, form, change) -> None:
        """Through the mailbox service: a new IMAP host, user or folder restarts the cursor and an empty password
        field leaves the stored (maybe undecryptable) secret alone."""
        if change:
            mailbox_service.update_mailbox(Mailbox.objects.get(pk=obj.pk), form.mailbox_changes())
        else:
            obj.pk = mailbox_service.create_mailbox(obj.channel, form.mailbox_changes()).pk

    def get_readonly_fields(self, request, obj=None) -> tuple:
        return (*self.readonly_fields, "channel") if obj else self.readonly_fields


@admin.register(HealthPolicy)
class HealthPolicyAdmin(admin.ModelAdmin):
    list_display = ("channel", "enabled", "window_hours", "min_sample")


@admin.register(MailboxPauseLog)
class MailboxPauseLogAdmin(admin.ModelAdmin):
    list_display = ("mailbox", "action", "trigger", "reason", "user", "created_at")
    list_filter = ("action", "trigger")

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


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
