import base64
import hashlib
from decimal import Decimal
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from PIL import Image
from rest_framework.authtoken.models import Token

from core.models import (
    Customer,
    InventoryMovement,
    KioskStation,
    Listing,
    ListingVariant,
    Payment,
    Product,
    ProductCategory,
    Role,
    SalesOrder,
    SalesOrderItem,
    SystemSettings,
    User,
)


def set_currency(**overrides):
    """
    Set the live currency configuration and return the settings row.

    Defaults to USD with bolívares as the secondary currency at 50 Bs per
    dollar, so a 10.00 payment is Bs 500.00.
    """
    settings = SystemSettings.get()
    values = {
        'currency_code': 'USD',
        'currency_symbol': '$',
        'decimal_places': 2,
        'secondary_currency_enabled': True,
        'secondary_currency_code': 'VES',
        'secondary_currency_symbol': 'Bs.',
        'secondary_decimal_places': 2,
        'secondary_exchange_rate': Decimal('50'),
    }
    values.update(overrides)
    for name, value in values.items():
        setattr(settings, name, value)
    settings.save()
    return settings


def make_role(name):
    return Role.objects.get_or_create(name=name)[0]


def make_user(role_name=Role.STAFF, email=None, password='Pass12345!', **attrs):
    role = make_role(role_name) if role_name else None
    email = email or f'{(role_name or "norole").lower()}-{User.objects.count()}@example.com'
    return User.objects.create_user(
        email=email,
        password=password,
        first_name=attrs.pop('first_name', role_name or 'No'),
        last_name=attrs.pop('last_name', 'Role'),
        role=role,
        **attrs,
    )


def auth_client(client, user):
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return token


def make_customer(**attrs):
    suffix = Customer.objects.count() + 1
    defaults = {
        'first_name': 'Jane',
        'last_name': f'Customer{suffix}',
        'email': f'customer-{suffix}@example.com',
        'national_id': f'V{10000000 + suffix}',
    }
    defaults.update(attrs)
    return Customer.objects.create(**defaults)


def make_category(name=None):
    return ProductCategory.objects.create(name=name or f'Category {ProductCategory.objects.count() + 1}')


def make_product(sku=None, stock=10, unit_price='10.00', **attrs):
    category = attrs.pop('category', None) or make_category()
    suffix = Product.objects.count() + 1
    product = Product.objects.create(
        sku=sku or f'SKU-{suffix:03d}',
        name=attrs.pop('name', f'Product {suffix}'),
        category=category,
        unit_price=Decimal(str(unit_price)),
        low_stock_threshold=attrs.pop('low_stock_threshold', 2),
        external_image_url=attrs.pop('external_image_url', 'https://cdn.example.com/product.png'),
        **attrs,
    )
    if stock:
        actor = attrs.get('created_by') or make_user(Role.MANAGER)
        InventoryMovement.objects.create(
            product=product,
            movement_type=InventoryMovement.PURCHASE,
            quantity=stock,
            reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
            reference_id=0,
            created_by=actor,
        )
    return product


def make_order(customer=None, product=None, user=None, status=SalesOrder.DRAFT, quantity=1, total=None):
    customer = customer or make_customer()
    product = product or make_product()
    user = user or make_user(Role.STAFF)
    order = SalesOrder.objects.create(
        customer=customer,
        status=status,
        created_by=user,
        confirmed_by=user if status in {
            SalesOrder.CONFIRMED,
            SalesOrder.PAID,
            SalesOrder.SHIPPED,
            SalesOrder.DELIVERED,
            SalesOrder.CANCELLED,
            SalesOrder.REFUNDED,
        } else None,
        confirmed_at=timezone.now() if status in {
            SalesOrder.CONFIRMED,
            SalesOrder.PAID,
            SalesOrder.SHIPPED,
            SalesOrder.DELIVERED,
            SalesOrder.CANCELLED,
            SalesOrder.REFUNDED,
        } else None,
    )
    line_total = total if total is not None else product.unit_price * quantity
    SalesOrderItem.objects.create(
        sales_order=order,
        product=product,
        quantity=quantity,
        unit_price=product.unit_price,
        line_total=line_total,
    )
    order.subtotal = Decimal(str(line_total))
    order.total_amount = Decimal(str(line_total))
    if status in {SalesOrder.PAID, SalesOrder.SHIPPED, SalesOrder.DELIVERED}:
        order.paid_at = timezone.now()
    order.save()
    return order


def make_payment(order=None, user=None, amount=None, **attrs):
    order = order or make_order(status=SalesOrder.CONFIRMED)
    user = user or make_user(Role.STAFF)
    return Payment.objects.create(
        sales_order=order,
        amount=Decimal(str(amount if amount is not None else order.total_amount)),
        payment_method=attrs.pop('payment_method', Payment.CASH),
        status=attrs.pop('status', Payment.CONFIRMED),
        reference_number=attrs.pop('reference_number', 'ref-001'),
        recorded_by=user,
        **attrs,
    )


def make_kiosk_station(raw_key='kiosk-key-1234567890', active=True, **attrs):
    admin = attrs.pop('admin', None) or make_user(Role.ADMIN, is_staff=True)
    service_user = attrs.pop('service_user', None) or make_user(
        Role.KIOSK,
        email=f'kiosk-{KioskStation.objects.count()}@example.com',
        password=None,
    )
    station = KioskStation.objects.create(
        store_identifier=attrs.pop('store_identifier', 'MAIN'),
        station_number=attrs.pop('station_number', KioskStation.objects.count() + 1),
        label=attrs.pop('label', 'Front'),
        api_key_prefix=raw_key[:8],
        api_key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
        service_user=service_user,
        created_by=admin,
        is_active=active,
        **attrs,
    )
    return station, raw_key


def png_upload(name='image.png', size=(8, 8), color='red'):
    buffer = BytesIO()
    Image.new('RGB', size, color=color).save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def png_data_url(size=(8, 8), color='blue'):
    buffer = BytesIO()
    Image.new('RGB', size, color=color).save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def vepay_payload(
    *,
    amount='500.00',
    currency='VES',
    reference='REF123',
    paid_on='2026-05-03',
    bank_app='BDV',
    transaction_key='tx-123',
    complete=True,
):
    return {
        'request_id': 'req-test',
        'payment': {
            'amount': {'value': amount, 'currency': currency},
            'reference': reference,
            'date_time': {'iso': paid_on},
            'bank_app': bank_app,
        },
        'origin': {'bank': bank_app, 'phone': '04121234567'},
        'recipient': {'bank': 'Banesco'},
        'validation': {
            'is_complete': complete,
            'missing_fields': [] if complete else ['payment.amount'],
        },
        'transaction_key': transaction_key,
    }


def make_channel(slug='trend-store', **attrs):
    """Provision a sales channel; returns ``(channel, raw_key)``."""
    from api.storefront.provisioning import create_channel

    admin = attrs.pop('admin', None) or make_user(Role.ADMIN, is_staff=True)
    return create_channel(
        name=attrs.pop('name', slug.replace('-', ' ').title()),
        slug=slug,
        created_by=admin,
        label=attrs.pop('label', 'first key'),
    )


def channel_auth(raw_key):
    """Request kwargs that authenticate as a channel."""
    return {'HTTP_AUTHORIZATION': f'ChannelKey {raw_key}'}


def make_listing(*products, slug=None, title=None, published=True, **attrs):
    """A listing with one variant per product, in the order given."""
    count = Listing.objects.count() + 1
    listing = Listing.objects.create(
        slug=slug or f'listing-{count}',
        title=title or f'Listing {count}',
        category=attrs.pop('category', None) or products[0].category,
        is_published=published,
        **attrs,
    )
    for position, product in enumerate(products):
        ListingVariant.objects.create(
            listing=listing, product=product, label=product.sku, position=position,
        )
    return listing


def make_channel_order(channel, product, *, reference='WEB-1', quantity=1, external_ref='user-1',
                       email='ana@example.com', fulfillment='pickup', minutes_old=None):
    """
    Create a channel order through the service, as the API would.

    The product must already be in a published listing. `minutes_old` moves
    its expiry into the past by that long beyond its natural lifetime, for
    expiry tests. Returns the ChannelOrder.
    """
    from datetime import timedelta

    from core.services.channel_orders import create_channel_order
    from core.services.customer_links import CustomerProfile
    from django.utils import timezone

    channel_order, _created = create_channel_order(
        channel,
        client_reference=reference,
        profile=CustomerProfile(
            external_ref=external_ref, email=email, first_name='Ana', last_name='Perez',
        ),
        items=[(product.sku, quantity)],
        fulfillment=fulfillment,
        contact={},
    )
    if minutes_old is not None:
        channel_order.expires_at = timezone.now() - timedelta(minutes=minutes_old)
        channel_order.save(update_fields=['expires_at'])
    return channel_order
