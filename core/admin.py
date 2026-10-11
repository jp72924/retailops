from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.db.models import Count, Q
from django.utils.html import format_html
from .models import (
    Role, User, Customer,
    ProductCategory, Product,
    SalesOrder, SalesOrderItem,
    CurrencySnapshot, Payment, InventoryMovement,
    SystemSettings, OcrCallLog,
    KioskStation,
    ChannelCustomerLink, ChannelKey, ChannelOrder, Listing, ListingVariant, SalesChannel,
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
    model = SalesOrderItem
    extra = 0
    fields = ('product', 'quantity', 'unit_price', 'tax_rate', 'line_total')
    readonly_fields = ('line_total',)


@admin.register(SalesOrder)
class SalesOrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'customer', 'status', 'total_amount', 'created_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('order_number', 'customer__first_name', 'customer__last_name', 'customer__email')
    readonly_fields = ('order_number', 'created_at', 'updated_at')
    raw_id_fields = ('customer', 'created_by', 'confirmed_by')
    inlines = [SalesOrderItemInline]


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('payment_number', 'sales_order', 'amount', 'payment_method', 'recorded_by', 'created_at')
    list_filter = ('payment_method',)
    search_fields = ('payment_number', 'sales_order__order_number', 'reference_number')
    # currency_snapshot is editable=False, so it never appears as a form
    # field: re-pointing a recorded payment at another rate is not an edit
    # this admin offers. It is shown read-only instead.
    readonly_fields = ('payment_number', 'recorded_currency', 'created_at')
    raw_id_fields = ('sales_order', 'recorded_by')

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
    list_filter = ('is_published', 'display_type', 'category')
    search_fields = ('title', 'slug', 'variants__product__sku', 'variants__product__name')
    prepopulated_fields = {'slug': ('title',)}
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
