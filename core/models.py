import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone


# ─── SequenceCounter ─────────────────────────────────────────────────────────

class SequenceCounter(models.Model):
    """
    Atomic per-prefix sequence counter used to generate unique order/payment
    numbers without race conditions.

    next_value(prefix) acquires a row-level lock via SELECT FOR UPDATE inside
    a transaction, increments last_value, and returns the new integer.  All
    concurrent callers for the same prefix are serialised through that lock,
    so two simultaneous requests can never get the same sequence number.
    """
    prefix     = models.CharField(max_length=30, unique=True)
    last_value = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = 'Sequence Counter'

    def __str__(self):
        return f'{self.prefix} → {self.last_value}'

    @classmethod
    def next_value(cls, prefix: str) -> int:
        with transaction.atomic():
            cls.objects.get_or_create(prefix=prefix, defaults={'last_value': 0})
            obj = cls.objects.select_for_update().get(prefix=prefix)
            obj.last_value += 1
            obj.save(update_fields=['last_value'])
            return obj.last_value


# ─── Role ────────────────────────────────────────────────────────────────────

class Role(models.Model):
    ADMIN = 'Admin'
    MANAGER = 'Manager'
    STAFF = 'Staff'
    KIOSK = 'Kiosk'
    NAME_CHOICES = [
        (ADMIN, 'Admin'),
        (MANAGER, 'Manager'),
        (STAFF, 'Staff'),
        (KIOSK, 'Kiosk'),
    ]

    name = models.CharField(max_length=20, choices=NAME_CHOICES, unique=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


# ─── User ─────────────────────────────────────────────────────────────────────

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Email address is required.')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if not extra_fields['is_staff']:
            raise ValueError('Superuser must have is_staff=True.')
        if not extra_fields['is_superuser']:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='users',
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # controls Django admin access
    timezone = models.CharField(max_length=64, default='UTC')
    language = models.CharField(max_length=10, default='en')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['first_name', 'last_name']

    objects = UserManager()

    def __str__(self):
        return f'{self.get_full_name()} <{self.email}>'

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()

    def get_short_name(self):
        return self.first_name

    @property
    def role_name(self):
        return self.role.name if self.role else None


# ─── Customer ─────────────────────────────────────────────────────────────────

class Customer(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='customers',
    )
    first_name = models.CharField(max_length=150)
    last_name = models.CharField(max_length=150)
    email = models.EmailField(unique=True)
    national_id = models.CharField(max_length=20, null=True, blank=True, unique=True, db_index=True)
    phone = models.CharField(max_length=30, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(
        max_length=1,
        choices=[('M', 'Masculino'), ('F', 'Femenino')],
        blank=True,
        default='',
    )
    address_line1 = models.CharField(max_length=255, blank=True)
    address_line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    country = models.CharField(max_length=100, default='United States')
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.first_name} {self.last_name}'

    def get_full_name(self):
        return f'{self.first_name} {self.last_name}'.strip()


# ─── ProductCategory ──────────────────────────────────────────────────────────

class ProductCategory(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    parent_category = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subcategories',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Product Category'
        verbose_name_plural = 'Product Categories'

    def __str__(self):
        if self.parent_category:
            return f'{self.parent_category} › {self.name}'
        return self.name


# ─── Product ──────────────────────────────────────────────────────────────────

PRODUCT_IMAGE_MAX_SIZE = 5 * 1024 * 1024
PRODUCT_IMAGE_ALLOWED_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']


def product_image_upload_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'jpg')[:12]
    safe_sku = ''.join(
        ch if ch.isalnum() or ch in {'-', '_'} else '-'
        for ch in (getattr(instance, 'sku', '') or 'product')
    )
    today = timezone.now()
    return f'products/{today:%Y/%m}/{safe_sku}-{uuid.uuid4().hex}.{ext}'


def validate_product_image_file(value):
    if not value:
        return
    size = getattr(value, 'size', None)
    if size is not None and size > PRODUCT_IMAGE_MAX_SIZE:
        raise ValidationError('Product image must be 5 MB or smaller.')


class Product(models.Model):
    PIECE = 'piece'
    KG = 'kg'
    LITER = 'liter'
    METER = 'meter'
    BOX = 'box'
    PACK = 'pack'
    UNIT_CHOICES = [
        (PIECE, 'Piece'),
        (KG, 'Kilogram'),
        (LITER, 'Liter'),
        (METER, 'Meter'),
        (BOX, 'Box'),
        (PACK, 'Pack'),
    ]

    sku = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        related_name='products',
    )
    unit_of_measure = models.CharField(max_length=20, choices=UNIT_CHOICES, default=PIECE)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    low_stock_threshold = models.PositiveIntegerField(default=10)
    image = models.ImageField(
        upload_to=product_image_upload_path,
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=PRODUCT_IMAGE_ALLOWED_EXTENSIONS),
            validate_product_image_file,
        ],
    )
    external_image_url = models.URLField(max_length=500, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'[{self.sku}] {self.name}'

    @property
    def has_image_source(self):
        return bool(self.image) or bool((self.external_image_url or '').strip())

    @property
    def primary_image_url(self):
        if self.image:
            return self.image.url
        return (self.external_image_url or '').strip()

    def clean(self):
        super().clean()
        if self.is_active and not self.has_image_source:
            raise ValidationError({
                'image': 'Active products need an uploaded image or an external image URL.',
            })

    @property
    def current_stock(self):
        result = self.inventory_movements.aggregate(
            total=models.Sum('quantity')
        )
        return result['total'] or 0

    @property
    def is_low_stock(self):
        stock = self.current_stock
        return 0 < stock <= self.low_stock_threshold

    @property
    def is_out_of_stock(self):
        return self.current_stock <= 0


# ─── SalesOrder ───────────────────────────────────────────────────────────────

class SalesOrder(models.Model):
    DRAFT = 'draft'
    PENDING = 'pending'
    CONFIRMED = 'confirmed'
    PAID = 'paid'
    SHIPPED = 'shipped'
    DELIVERED = 'delivered'
    CANCELLED = 'cancelled'
    REFUNDED = 'refunded'
    STATUS_CHOICES = [
        (DRAFT, 'Draft'),
        (PENDING, 'Pending'),
        (CONFIRMED, 'Confirmed'),
        (PAID, 'Paid'),
        (SHIPPED, 'Shipped'),
        (DELIVERED, 'Delivered'),
        (CANCELLED, 'Cancelled'),
        (REFUNDED, 'Refunded'),
    ]

    order_number = models.CharField(max_length=30, unique=True, blank=True, editable=False)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.PROTECT,
        related_name='orders',
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=DRAFT)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='created_orders',
    )
    confirmed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='confirmed_orders',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = self._generate_order_number()
        super().save(*args, **kwargs)

    def _generate_order_number(self):
        today = timezone.now().strftime('%Y%m%d')
        prefix = f'SO-{today}'
        seq = SequenceCounter.next_value(prefix)
        return f'{prefix}-{seq:04d}'

    def __str__(self):
        return self.order_number

    @property
    def amount_paid(self):
        result = self.payments.filter(status='confirmed').aggregate(total=models.Sum('amount'))
        return result['total'] or 0

    @property
    def amount_outstanding(self):
        return self.total_amount - self.amount_paid


# ─── SalesOrderItem ───────────────────────────────────────────────────────────

class SalesOrderItem(models.Model):
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.CASCADE,
        related_name='items',
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='order_items',
    )
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)  # price snapshot at order time
    tax_rate = models.DecimalField(max_digits=6, decimal_places=4, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        self.line_total = self.quantity * self.unit_price
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.sales_order.order_number} — {self.product.sku} × {self.quantity}'


# ─── Payment ──────────────────────────────────────────────────────────────────

def receipt_image_upload_path(instance, filename):
    ext = (filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'jpg')[:12]
    order_number = getattr(getattr(instance, 'sales_order', None), 'order_number', '') or 'unassigned'
    safe_order_number = ''.join(
        ch if ch.isalnum() or ch in {'-', '_'} else '-'
        for ch in order_number
    )
    today = timezone.now()
    return f'receipts/{today:%Y/%m}/{safe_order_number}-{uuid.uuid4().hex}.{ext}'


class Payment(models.Model):
    CASH = 'cash'
    MOBILE_PAYMENT = 'mobile_payment'
    BANK_TRANSFER = 'bank_transfer'
    CARD = 'card'
    CHECK = 'check'
    OTHER = 'other'
    PENDING_REVIEW = 'pending_review'
    CONFIRMED = 'confirmed'
    METHOD_CHOICES = [
        (CASH, 'Cash'),
        (MOBILE_PAYMENT, 'Mobile Payment'),
        (BANK_TRANSFER, 'Bank Transfer'),
        (CARD, 'Card'),
        (CHECK, 'Check'),
        (OTHER, 'Other'),
    ]
    STATUS_CHOICES = [
        (PENDING_REVIEW, 'Pending Review'),
        (CONFIRMED, 'Confirmed'),
    ]

    payment_number = models.CharField(max_length=30, unique=True, blank=True, editable=False)
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.PROTECT,
        related_name='payments',
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=CONFIRMED)
    reference_number = models.CharField(max_length=100, blank=True)
    receipt_image = models.ImageField(upload_to=receipt_image_upload_path, blank=True, null=True)
    ocr_receipt_data = models.JSONField(blank=True, null=True)
    transaction_key = models.CharField(max_length=128, blank=True, db_index=True)
    origin_phone = models.CharField(max_length=30, blank=True)
    origin_bank = models.CharField(max_length=120, blank=True)
    recipient_bank = models.CharField(max_length=120, blank=True)
    recipient_account = models.CharField(max_length=64, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='recorded_payments',
    )
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['transaction_key'],
                condition=models.Q(transaction_key__gt=''),
                name='payment_transaction_key_unique_when_set',
            ),
        ]

    def save(self, *args, **kwargs):
        if not self.payment_number:
            self.payment_number = self._generate_payment_number()
        super().save(*args, **kwargs)

    def _generate_payment_number(self):
        today = timezone.now().strftime('%Y%m%d')
        prefix = f'PAY-{today}'
        seq = SequenceCounter.next_value(prefix)
        return f'{prefix}-{seq:04d}'

    def __str__(self):
        return self.payment_number


# ─── InventoryMovement ────────────────────────────────────────────────────────

class InventoryMovement(models.Model):
    SALE = 'sale'
    PURCHASE = 'purchase'
    ADJUSTMENT = 'adjustment'
    RETURN = 'return'
    MOVEMENT_TYPE_CHOICES = [
        (SALE, 'Sale'),
        (PURCHASE, 'Purchase'),
        (ADJUSTMENT, 'Adjustment'),
        (RETURN, 'Return'),
    ]

    SALES_ORDER = 'SalesOrder'
    PURCHASE_ORDER = 'PurchaseOrder'
    MANUAL_ADJUSTMENT = 'ManualAdjustment'
    RETURN_REF = 'Return'
    REFERENCE_TYPE_CHOICES = [
        (SALES_ORDER, 'Sales Order'),
        (PURCHASE_ORDER, 'Purchase Order'),
        (MANUAL_ADJUSTMENT, 'Manual Adjustment'),
        (RETURN_REF, 'Return'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.PROTECT,
        related_name='inventory_movements',
    )
    movement_type = models.CharField(max_length=20, choices=MOVEMENT_TYPE_CHOICES)
    quantity = models.IntegerField()  # positive = addition, negative = deduction
    reference_type = models.CharField(max_length=30, choices=REFERENCE_TYPE_CHOICES)
    reference_id = models.PositiveIntegerField()
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='inventory_movements',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        sign = '+' if self.quantity >= 0 else ''
        return f'{self.product.sku} {sign}{self.quantity} ({self.get_movement_type_display()})'


# ─── SystemSettings ───────────────────────────────────────────────────────────

class SystemSettings(models.Model):
    """
    Singleton model for system-wide configuration.
    Only one row is ever created (pk=1 is enforced in save()).
    Use SystemSettings.get() to retrieve the instance.
    """
    currency_code   = models.CharField(max_length=3, default='USD')
    currency_symbol = models.CharField(max_length=4, default='$')
    decimal_places  = models.PositiveSmallIntegerField(default=2)

    secondary_currency_enabled = models.BooleanField(default=False)
    secondary_currency_code    = models.CharField(max_length=3, blank=True, default='')
    secondary_currency_symbol  = models.CharField(max_length=4, blank=True, default='')
    secondary_decimal_places   = models.PositiveSmallIntegerField(default=2)
    secondary_exchange_rate    = models.DecimalField(
        max_digits=20, decimal_places=8, default=Decimal('1')
    )

    ocr_enabled = models.BooleanField(default=False)
    ocr_provider = models.CharField(
        max_length=20,
        default='vepay',
        choices=[('vepay', 'VEPay')],
    )
    ocr_base_url = models.URLField(blank=True, default='')
    ocr_api_key = models.CharField(max_length=256, blank=True)
    ocr_timeout_seconds = models.PositiveIntegerField(default=30)
    ocr_max_file_mb = models.PositiveIntegerField(default=8)
    ocr_strict_amount = models.BooleanField(default=True)
    ocr_require_complete = models.BooleanField(default=False)
    ocr_enabled_methods = models.JSONField(default=list)
    receipt_image_required_for_receipt_methods = models.BooleanField(default=True)
    delete_receipt_image_after_days = models.PositiveIntegerField(default=90)

    class Meta:
        verbose_name = 'System Settings'
        verbose_name_plural = 'System Settings'

    def clean(self):
        if self.secondary_currency_enabled:
            if not self.secondary_currency_symbol.strip():
                raise ValidationError({
                    'secondary_currency_symbol': 'Required when secondary currency is enabled.'
                })
            if self.secondary_exchange_rate is None or self.secondary_exchange_rate <= 0:
                raise ValidationError({
                    'secondary_exchange_rate': 'Must be greater than zero.'
                })
        if self.ocr_enabled and not (self.ocr_base_url or '').strip():
            raise ValidationError({
                'ocr_base_url': 'Required when OCR is enabled.'
            })
        if self.delete_receipt_image_after_days <= 0:
            raise ValidationError({
                'delete_receipt_image_after_days': 'Must be greater than zero.'
            })

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def __str__(self):
        return f'System Settings ({self.currency_code})'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ─── KioskStation ────────────────────────────────────────────────────────────

class KioskStation(models.Model):
    """
    Represents a physical self-checkout kiosk terminal.

    Each station gets its own API key and an auto-created service User with the
    Kiosk role.  The service user satisfies all existing created_by / recorded_by
    FK constraints without schema changes to SalesOrder, Payment, or
    InventoryMovement.  The user's password is set to unusable so it cannot be
    used for normal login.

    The raw API key is shown once at provisioning time.  Only the SHA-256 hash
    and an 8-char prefix (for fast lookup) are stored.
    """
    store_identifier = models.CharField(max_length=50)
    station_number = models.PositiveSmallIntegerField()
    label = models.CharField(max_length=100, blank=True)
    api_key_prefix = models.CharField(max_length=8, db_index=True)
    api_key_hash = models.CharField(max_length=128)
    service_user = models.OneToOneField(
        User,
        on_delete=models.PROTECT,
        related_name='kiosk_station',
    )
    is_active = models.BooleanField(default=True)
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name='provisioned_stations',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('store_identifier', 'station_number')]
        verbose_name = 'Kiosk Station'

    def __str__(self):
        return f'{self.store_identifier} / Station {self.station_number}'


class OcrCallLog(models.Model):
    """
    Lightweight metadata audit for receipt OCR calls.

    Image bytes are deliberately not stored here; Payment.receipt_image is the
    evidence store, and retention is controlled separately.
    """
    kiosk_station = models.ForeignKey(
        KioskStation,
        on_delete=models.SET_NULL,
        related_name='ocr_call_logs',
        null=True,
        blank=True,
    )
    sales_order = models.ForeignKey(
        SalesOrder,
        on_delete=models.SET_NULL,
        related_name='ocr_call_logs',
        null=True,
        blank=True,
    )
    request_id = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=50, db_index=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    bytes_sent = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at'], name='ocr_created_idx'),
            models.Index(fields=['status', 'created_at'], name='ocr_status_created_idx'),
        ]

    def __str__(self):
        return f'OCR {self.status} ({self.sales_order_id or "no-order"})'

