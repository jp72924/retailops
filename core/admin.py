from django import forms
from django.contrib import admin, messages
from django.contrib.admin.utils import unquote
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.html import format_html
from .models import (
    Role, User, Customer,
    ProductCategory, Product,
    SalesOrder, SalesOrderItem,
    CurrencySnapshot, Payment, InventoryMovement,
    SystemSettings, OcrCallLog,
    KioskStation,
    ChannelCustomerLink, ChannelKey, ChannelOrder, Listing, ListingVariant, SalesChannel,
    ChannelEvent, ChannelWebhook, WebhookDelivery,
    Entitlement, ProductFulfillment,
)


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'description', 'created_at')
    search_fields = ('name',)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_active', 'is_staff', 'created_at')
    list_filter = ('role', 'is_active', 'is_staff')
    search_fields = ('email', 'first_name', 'last_name')
    ordering = ('email',)
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'role')}),
        ('Regional', {'fields': ('timezone', 'language')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'first_name', 'last_name', 'role', 'password1', 'password2'),
        }),
    )

    # Kiosk and Channel users belong to provisioned devices and integrations. They are
    # created by provisioning, never given a role or a password here, and never edited:
    # a service account with a password would be a person-shaped login into endpoints that
    # accept any authenticated user.
    SERVICE_USER_MESSAGE = 'Service accounts are managed by provisioning, not user management.'

    def get_readonly_fields(self, request, obj=None):
        readonly = super().get_readonly_fields(request, obj)
        if obj is not None and obj.is_service:
            return (
                *readonly, 'password', 'email', 'first_name', 'last_name', 'role', 'timezone',
                'language', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions',
                'last_login',
            )
        return readonly

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == 'role':
            kwargs['queryset'] = Role.objects.exclude(name__in=Role.SERVICE_NAMES)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def user_change_password(self, request, id, form_url=''):
        user = self.get_object(request, unquote(id))
        if user is not None and user.is_service:
            self.message_user(request, self.SERVICE_USER_MESSAGE, messages.ERROR)
            return HttpResponseRedirect(reverse('admin:core_user_change', args=[user.pk]))
        return super().user_change_password(request, id, form_url)


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('get_full_name', 'email', 'national_id', 'phone', 'city', 'country', 'created_at')
    list_filter = ('country',)
    search_fields = ('first_name', 'last_name', 'email', 'phone', 'national_id')
    raw_id_fields = ('user',)


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'parent_category', 'created_at')
    search_fields = ('name',)
    raw_id_fields = ('parent_category',)


class ProductFulfillmentInline(admin.StackedInline):
    """What a customer is given when they pay for the product; none means nothing is delivered."""
    model = ProductFulfillment
    extra = 0
    max_num = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'product_thumbnail', 'sku', 'name', 'category', 'unit_of_measure',
        'unit_price', 'kind', 'track_inventory', 'low_stock_threshold', 'is_active',
    )
    list_filter = ('category', 'unit_of_measure', 'kind', 'track_inventory', 'is_active')
    search_fields = ('sku', 'name')
    readonly_fields = ('image_preview',)
    inlines = [ProductFulfillmentInline]

    def product_thumbnail(self, obj):
        url = obj.primary_image_url
        if not url:
            return format_html('<span style="color:#94A3B8;">No image</span>')
        return format_html(
            '<img src="{}" alt="" style="width:40px;height:40px;object-fit:contain;border-radius:6px;">',
            url,
        )
    product_thumbnail.short_description = 'Image'

    def image_preview(self, obj):
        if not obj or not obj.primary_image_url:
            return 'No image'
        return format_html(
            '<img src="{}" alt="" style="max-width:240px;max-height:240px;object-fit:contain;border-radius:8px;">',
            obj.primary_image_url,
        )
    image_preview.short_description = 'Current image'


class SalesOrderItemInline(admin.TabularInline):
    """View-only: a line changes the order's total and the stock it holds."""
    model = SalesOrderItem
    extra = 0
    fields = ('product', 'quantity', 'unit_price', 'tax_rate', 'line_total')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer', 'status', 'total_amount', 'created_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('order_number', 'customer__first_name', 'customer__last_name', 'customer__email')
    # An order's status, money and stock move only through the services that lock the order,
    # apply its payments and write the stock ledger. Editing them here would skip all of that:
    # a status set by hand restores or deducts no stock, and a total set by hand contradicts the
    # payments. Only the notes are free text.
    readonly_fields = (
        'order_number', 'customer', 'status', 'subtotal', 'tax_amount', 'discount_amount',
        'total_amount', 'created_by', 'confirmed_by', 'created_at', 'updated_at',
        'confirmed_at', 'paid_at',
    )
    inlines = [SalesOrderItemInline]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_number', 'sales_order', 'amount', 'payment_method', 'recorded_by', 'created_at')
    list_filter = ('payment_method',)
    search_fields = ('payment_number', 'sales_order__order_number', 'reference_number')
    # A payment is a financial record. Its status, amount, order, transaction key, OCR reading
    # and review move only through the services that lock the order and apply the payment;
    # changing them here would leave the order's status and the stock out of step. currency_snapshot
    # is editable=False, so it never appears as a form field either: re-pointing a recorded payment
    # at another rate is not an edit this admin offers. Only the notes are free text.
    readonly_fields = (
        'payment_number', 'sales_order', 'amount', 'payment_method', 'status', 'reference_number',
        'receipt_image', 'ocr_receipt_data', 'transaction_key', 'origin_phone', 'origin_bank',
        'recipient_bank', 'recipient_account', 'verified_at', 'recorded_by', 'reviewed_by',
        'reviewed_at', 'review_notes', 'recorded_currency', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='Recorded currency')
    def recorded_currency(self, obj):
        snapshot = obj.currency_snapshot if obj.pk else None
        if snapshot is None:
            return '— (assigned when saved)'
        return f'{snapshot} · {snapshot.get_rate_source_display()}'


@admin.register(CurrencySnapshot)
class CurrencySnapshotAdmin(admin.ModelAdmin):
    """View-only: snapshots are immutable facts referenced by payments."""
    list_display = (
        'id', 'currency_code', 'secondary_currency_code', 'secondary_exchange_rate',
        'rate_as_of', 'rate_source', 'created_at',
    )
    list_filter = ('rate_source',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OcrCallLog)
class OcrCallLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'status', 'sales_order', 'kiosk_station', 'channel', 'latency_ms', 'bytes_sent', 'request_id')
    list_filter = ('status', 'channel', 'created_at')
    search_fields = ('request_id', 'sales_order__order_number')
    readonly_fields = ('kiosk_station', 'channel', 'sales_order', 'request_id', 'status', 'latency_ms', 'bytes_sent', 'created_at')
    raw_id_fields = ('kiosk_station', 'channel', 'sales_order')

    def has_add_permission(self, request):
        return False


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'reference_type', 'reference_id', 'created_by', 'created_at')
    list_filter = ('movement_type', 'reference_type')
    search_fields = ('product__sku', 'product__name')
    readonly_fields = ('created_at',)
    raw_id_fields = ('product', 'created_by')


@admin.register(SystemSettings)
class SystemSettingsAdmin(admin.ModelAdmin):
    list_display = (
        'currency_code',
        'currency_symbol',
        'decimal_places',
        'receipt_image_required_for_receipt_methods',
    )

    def has_add_permission(self, request):
        return not SystemSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(KioskStation)
class KioskStationAdmin(admin.ModelAdmin):
    list_display = ('store_identifier', 'station_number', 'label', 'is_active',
                    'last_heartbeat', 'created_at')
    list_filter = ('is_active', 'store_identifier')
    search_fields = ('store_identifier', 'label')
    readonly_fields = ('api_key_prefix', 'api_key_hash', 'service_user',
                       'last_heartbeat', 'created_at', 'updated_at')
    raw_id_fields = ('created_by',)
    actions = ['rotate_api_keys', 'deactivate_stations', 'activate_stations']

    @admin.action(description='Rotate API key (invalidates old key; shows new key once)')
    def rotate_api_keys(self, request, queryset):
        from api.kiosk.provisioning import rotate_api_key
        for station in queryset:
            raw_key = rotate_api_key(station)
            messages.warning(
                request,
                f'{station}: new API key (copy now — shown only once): {raw_key}',
            )

    @admin.action(description='Deactivate selected stations')
    def deactivate_stations(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} station(s) deactivated.', messages.SUCCESS)

    @admin.action(description='Activate selected stations')
    def activate_stations(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} station(s) activated.', messages.SUCCESS)


class ChannelKeyInline(admin.TabularInline):
    model = ChannelKey
    extra = 0
    can_delete = False
    fields = ('prefix', 'label', 'created_at', 'revoked_at')
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SalesChannel)
class SalesChannelAdmin(admin.ModelAdmin):
    """
    Channels are provisioned with `manage.py provision_channel`, which creates
    the service user and the first key; the admin manages them afterwards.
    """
    list_display = ('name', 'slug', 'is_active', 'active_keys', 'created_at')
    list_filter = ('is_active',)
    search_fields = ('name', 'slug')
    readonly_fields = ('slug', 'service_user', 'created_by', 'created_at', 'updated_at')
    inlines = [ChannelKeyInline]
    actions = ['issue_key', 'revoke_all_keys', 'deactivate_channels', 'activate_channels']

    def has_add_permission(self, request):
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            _active_keys=Count('keys', filter=Q(keys__revoked_at__isnull=True)),
        )

    @admin.display(description='Active keys', ordering='_active_keys')
    def active_keys(self, obj):
        return obj._active_keys

    @admin.action(description='Issue a new key (existing keys keep working; shown once)')
    def issue_key(self, request, queryset):
        from api.storefront.provisioning import issue_key
        for channel in queryset:
            _key, raw_key = issue_key(channel, label='issued in admin')
            messages.warning(
                request,
                f'{channel}: new channel key (copy now — shown only once): {raw_key}',
            )

    @admin.action(description='Revoke all keys of the selected channels')
    def revoke_all_keys(self, request, queryset):
        from api.storefront.provisioning import revoke_all_keys
        revoked = sum(revoke_all_keys(channel) for channel in queryset)
        self.message_user(request, f'{revoked} key(s) revoked.', messages.SUCCESS)

    @admin.action(description='Deactivate selected channels')
    def deactivate_channels(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f'{updated} channel(s) deactivated.', messages.SUCCESS)

    @admin.action(description='Activate selected channels')
    def activate_channels(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} channel(s) activated.', messages.SUCCESS)


@admin.register(ChannelKey)
class ChannelKeyAdmin(admin.ModelAdmin):
    """Keys are issued from the channel; here an operator can revoke one."""
    list_display = ('channel', 'prefix', 'label', 'created_at', 'revoked_at')
    list_filter = ('channel',)
    search_fields = ('prefix', 'label', 'channel__name')
    readonly_fields = ('channel', 'prefix', 'key_hash', 'label', 'created_at', 'revoked_at')
    actions = ['revoke_keys']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Revoke selected keys', permissions=['change'])
    def revoke_keys(self, request, queryset):
        from api.storefront.provisioning import revoke_key
        revoked = sum(revoke_key(key) for key in queryset)
        self.message_user(request, f'{revoked} key(s) revoked.', messages.SUCCESS)


class ListingVariantInline(admin.TabularInline):
    model = ListingVariant
    extra = 1
    raw_id_fields = ('product',)


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'category', 'display_type', 'badge', 'is_published', 'sort')
    list_editable = ('is_published', 'sort')
    list_filter = ('is_published', 'display_type', 'category', 'channels')
    search_fields = ('title', 'slug', 'variants__product__sku', 'variants__product__name')
    prepopulated_fields = {'slug': ('title',)}
    # Empty means every channel; naming some restricts the listing to them.
    filter_horizontal = ('channels',)
    inlines = [ListingVariantInline]
    actions = ['publish_listings', 'unpublish_listings']

    @admin.action(description='Publish selected listings')
    def publish_listings(self, request, queryset):
        updated = queryset.update(is_published=True)
        self.message_user(request, f'{updated} listing(s) published.', messages.SUCCESS)

    @admin.action(description='Unpublish selected listings')
    def unpublish_listings(self, request, queryset):
        updated = queryset.update(is_published=False)
        self.message_user(request, f'{updated} listing(s) unpublished.', messages.SUCCESS)


@admin.register(ChannelOrder)
class ChannelOrderAdmin(admin.ModelAdmin):
    """What came in through a channel. View-only: the order itself is managed as any other."""
    list_display = ('client_reference', 'channel', 'sales_order', 'fulfillment', 'expires_at', 'created_at')
    list_filter = ('channel', 'fulfillment')
    search_fields = ('client_reference', 'sales_order__order_number')
    list_select_related = ('channel', 'sales_order')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ChannelCustomerLink)
class ChannelCustomerLinkAdmin(admin.ModelAdmin):
    """A store's customer and the RetailOps customer it is tied to. View-only."""
    list_display = ('channel', 'external_ref', 'customer', 'created_at')
    list_filter = ('channel',)
    search_fields = ('external_ref', 'customer__email', 'customer__first_name', 'customer__last_name')
    list_select_related = ('channel', 'customer')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    """
    What was delivered for paid orders. View-only, and it never shows a value:
    a licence key appears masked, as everywhere else outside the one reveal.
    """
    list_display = (
        'id', 'order_number', 'product_sku', 'kind', 'status', 'masked_value',
        'seats', 'activated_at', 'expires_at', 'revealed_at',
    )
    list_filter = ('status', 'kind')
    search_fields = ('order_item__sales_order__order_number', 'order_item__product__sku')
    list_select_related = ('order_item__sales_order', 'order_item__product')
    # Named, so `value` is not rendered even in the read-only detail page.
    fields = (
        'order_item', 'kind', 'masked_value', 'seats', 'duration_days', 'status',
        'activated_at', 'expires_at', 'revealed_at', 'revoked_at', 'created_at',
    )
    readonly_fields = fields

    @admin.display(description='Order', ordering='order_item__sales_order__order_number')
    def order_number(self, obj):
        return obj.order_item.sales_order.order_number

    @admin.display(description='Product')
    def product_sku(self, obj):
        return obj.order_item.product.sku

    @admin.display(description='Value')
    def masked_value(self, obj):
        return obj.display_value or '-'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
# ── Events and webhooks ──────────────────────────────────────────────────────

class ChannelWebhookForm(forms.ModelForm):
    class Meta:
        model = ChannelWebhook
        fields = ('channel', 'url', 'label', 'is_active')

    def clean_url(self):
        from core.services.channel_webhooks import UnsafeTarget, check_url_shape
        url = self.cleaned_data['url']
        try:
            check_url_shape(url)
        except UnsafeTarget as exc:
            raise forms.ValidationError(str(exc))
        return url


@admin.register(ChannelWebhook)
class ChannelWebhookAdmin(admin.ModelAdmin):
    """
    Where a channel's events are sent. Registering an address is done here and nowhere a channel's
    own key can reach: a leaked key must not be able to name a destination. The signing secret is
    shown once, when the address is added or its secret rotated; only an encrypted copy is kept.
    """
    form = ChannelWebhookForm
    list_display = ('channel', 'url', 'label', 'is_active', 'consecutive_failures', 'disabled_at', 'has_previous_secret')
    list_filter = ('channel', 'is_active')
    search_fields = ('url', 'label', 'channel__name')
    readonly_fields = ('consecutive_failures', 'disabled_at', 'disabled_reason', 'created_by', 'created_at', 'updated_at')
    actions = ['rotate_secrets', 'clear_previous_secrets', 'reactivate_webhooks']

    def get_fields(self, request, obj=None):
        fields = ['channel', 'url', 'label', 'is_active']
        if obj is not None:
            fields += ['consecutive_failures', 'disabled_at', 'disabled_reason', 'created_by', 'created_at', 'updated_at']
        return fields

    def get_readonly_fields(self, request, obj=None):
        if obj is None:
            return ()
        return ('channel',) + self.readonly_fields

    @admin.display(boolean=True, description='Rotating')
    def has_previous_secret(self, obj):
        return bool(obj.previous_secret)

    def save_model(self, request, obj, form, change):
        from core.services import secret_box
        from core.services.channel_webhooks import new_secret
        if change:
            super().save_model(request, obj, form, change)
            return
        secret = new_secret()
        obj.secret = secret_box.seal(secret)
        obj.created_by = request.user
        super().save_model(request, obj, form, change)
        messages.warning(request, f'Signing secret for {obj.url} (copy it now, it is shown only once): {secret}')

    @admin.action(description='Rotate the signing secret (both sign until the old one is cleared; shown once)')
    def rotate_secrets(self, request, queryset):
        from core.services.channel_webhooks import rotate_secret
        for webhook in queryset:
            secret = rotate_secret(webhook)
            messages.warning(request, f'New signing secret for {webhook.url} (copy it now, it is shown only once): {secret}')

    @admin.action(description='Clear the previous signing secret (finish a rotation)')
    def clear_previous_secrets(self, request, queryset):
        from core.services.channel_webhooks import clear_previous_secret
        for webhook in queryset:
            clear_previous_secret(webhook)
        self.message_user(request, f'{queryset.count()} webhook(s) now sign with the current secret only.', messages.SUCCESS)

    @admin.action(description='Switch back on, and send what was waiting')
    def reactivate_webhooks(self, request, queryset):
        from core.services.channel_webhooks import reactivate
        released = sum(reactivate(webhook) for webhook in queryset)
        self.message_user(request, f'{queryset.count()} webhook(s) switched on; {released} delivery(ies) released.', messages.SUCCESS)


@admin.register(WebhookDelivery)
class WebhookDeliveryAdmin(admin.ModelAdmin):
    """The trail of every delivery: how often it was tried and what the receiver said. View-only."""
    list_display = ('id', 'webhook', 'event', 'order', 'state', 'attempts', 'last_status_code', 'next_attempt_at', 'delivered_at')
    list_filter = ('state', 'webhook__channel')
    search_fields = ('event__order_number', 'webhook__url')
    list_select_related = ('webhook__channel', 'event')
    readonly_fields = [field.name for field in WebhookDelivery._meta.fields]
    actions = ['retry_now']

    @admin.display(description='Order', ordering='event__order_number')
    def order(self, delivery):
        return delivery.event.order_number

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.action(description='Try again now', permissions=['change'])
    def retry_now(self, request, queryset):
        from django.utils import timezone
        updated = queryset.update(state=WebhookDelivery.PENDING, attempts=0, next_attempt_at=timezone.now())
        self.message_user(request, f'{updated} delivery(ies) queued.', messages.SUCCESS)


@admin.register(ChannelEvent)
class ChannelEventAdmin(admin.ModelAdmin):
    """The feed of a channel, as the store reads it. View-only."""
    list_display = ('channel', 'sequence', 'event_type', 'order_number', 'created_at')
    list_filter = ('channel', 'event_type')
    search_fields = ('order_number',)
    readonly_fields = [field.name for field in ChannelEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
