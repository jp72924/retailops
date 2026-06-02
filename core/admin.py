from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.utils.html import format_html
from .models import (
    Role, User, Customer,
    ProductCategory, Product,
    SalesOrder, SalesOrderItem,
    Payment, InventoryMovement,
    SystemSettings, OcrCallLog,
    KioskStation,
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


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = (
        'product_thumbnail', 'sku', 'name', 'category', 'unit_of_measure',
        'unit_price', 'low_stock_threshold', 'is_active',
    )
    list_filter = ('category', 'unit_of_measure', 'is_active')
    search_fields = ('sku', 'name')
    readonly_fields = ('image_preview',)

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
    readonly_fields = ('payment_number', 'created_at')
    raw_id_fields = ('sales_order', 'recorded_by')


@admin.register(OcrCallLog)
class OcrCallLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'status', 'sales_order', 'kiosk_station', 'latency_ms', 'bytes_sent', 'request_id')
    list_filter = ('status', 'created_at')
    search_fields = ('request_id', 'sales_order__order_number')
    readonly_fields = ('kiosk_station', 'sales_order', 'request_id', 'status', 'latency_ms', 'bytes_sent', 'created_at')
    raw_id_fields = ('kiosk_station', 'sales_order')

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

