"""
Seed the RetailOps database with realistic sample data.

Usage:
    python manage.py seed           # seed (skips if data already exists)
    python manage.py seed --force   # drop all sample data first, then re-seed
"""

import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core.models import (
    Customer, InventoryMovement, Payment,
    Product, ProductCategory, Role,
    SalesOrder, SalesOrderItem, User,
)


# ── Sample data definitions ────────────────────────────────────────────────────

CATEGORIES = [
    # (name, parent_name, description)
    ('Electronics',    None,          'Consumer electronics and gadgets'),
    ('Accessories',    'Electronics', 'Cables, peripherals, and add-ons'),
    ('Apparel',        None,          'Clothing and wearables'),
    ('Footwear',       'Apparel',     'Shoes, boots, and sandals'),
    ('Office Supplies', None,         'Stationery and workspace essentials'),
]

PRODUCTS = [
    # (sku, name, category, unit, price, threshold, description)
    ('MOUSE-WL-01',  'Wireless Optical Mouse',        'Accessories',    'piece',  Decimal('29.99'), 15,
     'Ergonomic 2.4 GHz wireless mouse, 1600 DPI, USB nano-receiver included.'),
    ('HUB-USBC-7P',  'USB-C Hub 7-Port',              'Accessories',    'piece',  Decimal('49.99'), 10,
     '7-in-1 USB-C hub: HDMI 4K, 3× USB-A, SD/MicroSD, 100W PD charging.'),
    ('KB-MECH-BL',   'Mechanical Keyboard (Blue)',    'Electronics',    'piece',  Decimal('89.99'), 8,
     'Tenkeyless mechanical keyboard, Cherry MX Blue switches, RGB backlit.'),
    ('STAND-LT-ALU', 'Aluminium Laptop Stand',        'Office Supplies','piece',  Decimal('35.00'), 10,
     'Adjustable aluminium stand, fits 11–17″ laptops, foldable.'),
    ('SHOE-RUN-42',  'Running Shoes (EU 42)',          'Footwear',       'piece',  Decimal('75.00'), 12,
     'Lightweight mesh running shoes, EU size 42, unisex.'),
    ('TEE-CTN-M',    'Cotton T-Shirt (Medium)',        'Apparel',        'piece',  Decimal('19.99'), 20,
     '100% cotton crew-neck t-shirt, medium, available in white.'),
    ('PENS-BP-10',   'Ballpoint Pens (Pack of 10)',   'Office Supplies','pack',   Decimal('4.99'),  25,
     'Medium-tip blue ballpoint pens, 10-pack.'),
    ('NB-A4-RULED',  'A4 Ruled Notebook',             'Office Supplies','piece',  Decimal('3.49'),  30,
     '96-page ruled A4 notebook, hard cover, wire-bound.'),
]

# Initial stock levels to add via Purchase movements
INITIAL_STOCK = {
    'MOUSE-WL-01':  100,
    'HUB-USBC-7P':   80,
    'KB-MECH-BL':    50,
    'STAND-LT-ALU':  40,
    'SHOE-RUN-42':   60,
    'TEE-CTN-M':    200,
    'PENS-BP-10':   150,
    'NB-A4-RULED':  120,
}

CUSTOMERS = [
    # (first, last, national_id, email, phone, addr1, city, state, postal, country, dob, gender, notes)
    ('María',      'González',  'V12345678',  'maria.gonzalez@example.com',
     '0412-1234567', 'Av. Libertador, Edif. Centro, Piso 3',
     'Caracas',        'Distrito Capital', '1010', 'Venezuela',
     datetime.date(1990, 3, 15),  'F', 'Clienta frecuente. Prefiere contacto por correo.'),
    ('Carlos',     'Hernández', 'V23456789',  'carlos.hernandez@example.com',
     '0414-2345678', 'Calle 100, Urb. Trigal Norte, Casa 12',
     'Valencia',       'Carabobo',         '2001', 'Venezuela',
     datetime.date(1985, 7, 22),  'M', ''),
    ('Sofía',      'Martínez',  'V34567890',  'sofia.martinez@example.com',
     '0416-3456789', 'Av. 5 de Julio, C.C. Las Américas, Local 8',
     'Maracaibo',      'Zulia',             '4001', 'Venezuela',
     datetime.date(1995, 11, 8),  'F', 'Compras al mayor para reventa. Solicita factura en entrega.'),
    ('Jesús',      'Rodríguez', 'V45678901',  'jesus.rodriguez@example.com',
     '0424-4567890', 'Carrera 19, Barrio El Carmen, Casa 45',
     'Barquisimeto',   'Lara',              '3001', 'Venezuela',
     datetime.date(1978, 4, 30),  'M', ''),
    ('Ana',        'López',     'V56789012',  'ana.lopez@example.com',
     '0426-5678901', 'Av. Las Delicias, Res. El Lago, Apto 7B',
     'Maracay',        'Aragua',            '2101', 'Venezuela',
     datetime.date(2000, 1, 20),  'F', 'Solo despacho en dirección de oficina.'),
    ('Miguel',     'Pérez',     'V67890123',  'miguel.perez@example.com',
     '0412-6789012', 'Calle Monagas, Sector Centro, Casa 3',
     'Maturín',        'Monagas',           '6201', 'Venezuela',
     datetime.date(1992, 9, 5),   'M', ''),
    ('Laura',      'Ramírez',   'V78901234',  'laura.ramirez@example.com',
     '0414-7890123', 'Av. Santiago Mariño, Urb. El Valle, Qta. 22',
     'Porlamar',       'Nueva Esparta',     '6301', 'Venezuela',
     datetime.date(1988, 6, 11),  'F', ''),
    ('Andrés',     'Torres',    'V89012345',  'andres.torres@example.com',
     '0416-8901234', 'Calle Páez, Sector Las Lomas, Casa 9',
     'Barinas',        'Barinas',           '5201', 'Venezuela',
     datetime.date(1975, 12, 3),  'M', 'Pago preferido en efectivo.'),
    ('Valentina',  'Flores',    'E10234567',  'valentina.flores@example.com',
     '0424-9012345', 'Av. Miranda, Res. Los Pinos, Piso 2, Apto 2A',
     'Los Teques',     'Miranda',           '1201', 'Venezuela',
     datetime.date(2003, 8, 27),  'F', 'Documentación de extranjera (cédula E).'),
    ('Roberto',    'Castillo',  'V11223344',  'roberto.castillo@example.com',
     '0426-0123456', 'Calle 4, Urb. La Concordia, Casa 17',
     'San Cristóbal',  'Táchira',           '5001', 'Venezuela',
     datetime.date(1968, 5, 19),  'M', ''),
]


class Command(BaseCommand):
    help = 'Populate the database with sample data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--force',
            action='store_true',
            help='Delete all existing sample data before seeding',
        )

    def handle(self, *args, **options):
        if options['force']:
            self._clear()

        if SalesOrder.objects.exists() or Customer.objects.exists():
            self.stdout.write(self.style.WARNING(
                'Sample data already exists. Run with --force to re-seed.'
            ))
            return

        with transaction.atomic():
            admin    = User.objects.filter(email='admin@retailops.local').first()
            manager  = User.objects.filter(email='manager@retailops.local').first()
            staff    = User.objects.filter(email='staff@retailops.local').first()

            if not all([admin, manager, staff]):
                self.stdout.write(self.style.ERROR(
                    'Seeded user accounts not found. Run: python manage.py migrate'
                ))
                return

            cats      = self._seed_categories()
            products  = self._seed_products(cats)
            customers = self._seed_customers()
            self._seed_initial_stock(products, manager)
            self._seed_orders(customers, products, admin, manager, staff)

        self.stdout.write(self.style.SUCCESS('Database seeded successfully.'))
        self._print_summary()

    # ── Clear ────────────────────────────────────────────────────────────────

    def _clear(self):
        self.stdout.write('Clearing existing data…')
        InventoryMovement.objects.all().delete()
        Payment.objects.all().delete()
        SalesOrderItem.objects.all().delete()
        SalesOrder.objects.all().delete()
        Customer.objects.all().delete()
        Product.objects.all().delete()
        ProductCategory.objects.all().delete()
        self.stdout.write(self.style.WARNING('Existing data cleared.'))

    # ── Categories ────────────────────────────────────────────────────────────

    def _seed_categories(self):
        self.stdout.write('  Creating product categories…')
        cats = {}
        # First pass: top-level
        for name, parent_name, desc in CATEGORIES:
            if parent_name is None:
                cat, _ = ProductCategory.objects.get_or_create(
                    name=name, defaults={'description': desc}
                )
                cats[name] = cat
        # Second pass: children
        for name, parent_name, desc in CATEGORIES:
            if parent_name is not None:
                cat, _ = ProductCategory.objects.get_or_create(
                    name=name,
                    defaults={'description': desc, 'parent_category': cats[parent_name]},
                )
                cats[name] = cat
        return cats

    # ── Products ─────────────────────────────────────────────────────────────

    def _seed_products(self, cats):
        self.stdout.write('  Creating products…')
        products = {}
        for sku, name, cat_name, unit, price, threshold, desc in PRODUCTS:
            p, _ = Product.objects.get_or_create(
                sku=sku,
                defaults={
                    'name': name,
                    'category': cats[cat_name],
                    'unit_of_measure': unit,
                    'unit_price': price,
                    'low_stock_threshold': threshold,
                    'description': desc,
                    'is_active': True,
                },
            )
            products[sku] = p
        return products

    # ── Customers ────────────────────────────────────────────────────────────

    def _seed_customers(self):
        self.stdout.write('  Creating customers…')
        customers = []
        for (first, last, national_id, email, phone, addr1,
             city, state, postal, country, dob, gender, notes) in CUSTOMERS:
            c, _ = Customer.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': first, 'last_name': last,
                    'national_id': national_id,
                    'phone': phone, 'address_line1': addr1,
                    'city': city, 'state': state,
                    'postal_code': postal, 'country': country,
                    'date_of_birth': dob,
                    'gender': gender,
                    'notes': notes,
                },
            )
            customers.append(c)
        return customers

    # ── Initial stock ─────────────────────────────────────────────────────────

    def _seed_initial_stock(self, products, created_by):
        self.stdout.write('  Recording initial stock (purchase movements)…')
        for sku, qty in INITIAL_STOCK.items():
            product = products[sku]
            InventoryMovement.objects.create(
                product=product,
                movement_type=InventoryMovement.PURCHASE,
                quantity=qty,
                reference_type=InventoryMovement.PURCHASE_ORDER,
                reference_id=1,
                notes='Opening stock — initial inventory load',
                created_by=created_by,
            )

    # ── Orders ────────────────────────────────────────────────────────────────

    def _seed_orders(self, customers, products, admin, manager, staff):
        self.stdout.write('  Creating sales orders…')

        maria, carlos, sofia, jesus, ana, miguel, laura, andres, valentina, roberto = customers
        mouse   = products['MOUSE-WL-01']
        hub     = products['HUB-USBC-7P']
        kb      = products['KB-MECH-BL']
        stand   = products['STAND-LT-ALU']
        shoes   = products['SHOE-RUN-42']
        tee     = products['TEE-CTN-M']
        pens    = products['PENS-BP-10']
        nb      = products['NB-A4-RULED']

        # ── 1. Draft ─────────────────────────────────────────────────────────
        o1 = self._create_order(maria, staff, SalesOrder.DRAFT, notes='Cliente llamó — necesita despacho urgente.')
        self._add_items(o1, [(mouse, 2, mouse.unit_price), (kb, 1, kb.unit_price)])

        # ── 2. Pending (submitted by staff, awaiting manager approval) ────────
        o2 = self._create_order(carlos, staff, SalesOrder.PENDING, notes='Solicitó empaque de regalo.')
        self._add_items(o2, [(hub, 3, hub.unit_price)])

        # ── 3. Confirmed (inventory deducted) ────────────────────────────────
        o3 = self._create_order(sofia, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager,
                                notes='Compra al mayor para reventa. Sin impuesto aplicado.')
        self._add_items(o3, [(shoes, 2, shoes.unit_price), (tee, 5, tee.unit_price)])
        self._deduct_stock(o3, manager)

        # ── 4. Confirmed + partial payment (still Confirmed, not yet Paid) ───
        o4 = self._create_order(jesus, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager,
                                notes='Pago parcial recibido por adelantado.')
        self._add_items(o4, [(stand, 3, stand.unit_price), (hub, 1, hub.unit_price)])
        self._deduct_stock(o4, manager)
        self._add_payment(o4, Decimal('50.00'), Payment.CASH, '', 'Depósito recibido.', staff)

        # ── 5. Paid in full ───────────────────────────────────────────────────
        o5 = self._create_order(ana, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager)
        self._add_items(o5, [(stand, 2, stand.unit_price)])
        self._deduct_stock(o5, manager)
        self._add_payment(o5, o5.total_amount, Payment.BANK_TRANSFER,
                          'TXN-20260401-8821', '', staff)
        o5.status  = SalesOrder.PAID
        o5.paid_at = timezone.now()
        o5.save()

        # ── 6. Shipped ────────────────────────────────────────────────────────
        o6 = self._create_order(miguel, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager,
                                notes='Entrega express solicitada.')
        self._add_items(o6, [(pens, 5, pens.unit_price), (nb, 3, nb.unit_price)])
        self._deduct_stock(o6, manager)
        self._add_payment(o6, o6.total_amount, Payment.CARD,
                          'CARD-7749-AUTH', '', staff)
        o6.status  = SalesOrder.PAID
        o6.paid_at = timezone.now()
        o6.save()
        o6.status = SalesOrder.SHIPPED
        o6.save()

        # ── 7. Delivered (maria's second order) ───────────────────────────────
        o7 = self._create_order(maria, manager, SalesOrder.CONFIRMED,
                                confirmed_by=manager)
        self._add_items(o7, [(mouse, 1, mouse.unit_price), (pens, 2, pens.unit_price)])
        self._deduct_stock(o7, manager)
        self._add_payment(o7, o7.total_amount, Payment.CASH, '', '', staff)
        o7.status  = SalesOrder.PAID
        o7.paid_at = timezone.now()
        o7.save()
        o7.status = SalesOrder.SHIPPED
        o7.save()
        o7.status = SalesOrder.DELIVERED
        o7.save()

        # ── 8. Cancelled (stock restored) ─────────────────────────────────────
        o8 = self._create_order(carlos, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager,
                                notes='Cliente solicitó cancelación tras confirmación.')
        self._add_items(o8, [(kb, 1, kb.unit_price)])
        self._deduct_stock(o8, manager)
        # Restore stock on cancel
        for item in o8.items.all():
            InventoryMovement.objects.create(
                product=item.product,
                movement_type=InventoryMovement.RETURN,
                quantity=item.quantity,
                reference_type=InventoryMovement.SALES_ORDER,
                reference_id=o8.pk,
                notes=f'Stock restaurado al cancelar {o8.order_number}',
                created_by=manager,
            )
        o8.status = SalesOrder.CANCELLED
        o8.save()

        # ── 9. Delivered (laura) ──────────────────────────────────────────────
        o9 = self._create_order(laura, staff, SalesOrder.CONFIRMED,
                                confirmed_by=manager)
        self._add_items(o9, [(tee, 3, tee.unit_price), (nb, 2, nb.unit_price)])
        self._deduct_stock(o9, manager)
        self._add_payment(o9, o9.total_amount, Payment.CARD,
                          'CARD-5512-AUTH', '', staff)
        o9.status  = SalesOrder.PAID
        o9.paid_at = timezone.now()
        o9.save()
        o9.status = SalesOrder.SHIPPED
        o9.save()
        o9.status = SalesOrder.DELIVERED
        o9.save()

        # ── 10. Paid (andres) ─────────────────────────────────────────────────
        o10 = self._create_order(andres, staff, SalesOrder.CONFIRMED,
                                 confirmed_by=manager,
                                 notes='Pago en efectivo confirmado.')
        self._add_items(o10, [(pens, 4, pens.unit_price), (mouse, 1, mouse.unit_price)])
        self._deduct_stock(o10, manager)
        self._add_payment(o10, o10.total_amount, Payment.CASH, '', '', staff)
        o10.status  = SalesOrder.PAID
        o10.paid_at = timezone.now()
        o10.save()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _create_order(self, customer, created_by, status,
                      confirmed_by=None, notes=''):
        order = SalesOrder.objects.create(
            customer=customer,
            status=status,
            notes=notes,
            created_by=created_by,
            confirmed_by=confirmed_by,
            confirmed_at=timezone.now() if confirmed_by else None,
        )
        return order

    def _add_items(self, order, line_items):
        """
        line_items: list of (product, quantity, unit_price).
        Saves each SalesOrderItem (line_total computed in save()),
        then recalculates and saves order totals.
        """
        subtotal = Decimal('0.00')
        for product, qty, price in line_items:
            item = SalesOrderItem.objects.create(
                sales_order=order,
                product=product,
                quantity=qty,
                unit_price=price,
            )
            subtotal += item.line_total
        order.subtotal      = subtotal
        order.tax_amount    = Decimal('0.00')
        order.discount_amount = Decimal('0.00')
        order.total_amount  = subtotal
        order.save()

    def _deduct_stock(self, order, created_by):
        for item in order.items.select_related('product').all():
            InventoryMovement.objects.create(
                product=item.product,
                movement_type=InventoryMovement.SALE,
                quantity=-item.quantity,
                reference_type=InventoryMovement.SALES_ORDER,
                reference_id=order.pk,
                notes=f'Stock deducted on confirmation of {order.order_number}',
                created_by=created_by,
            )

    def _add_payment(self, order, amount, method, reference, notes, recorded_by):
        Payment.objects.create(
            sales_order=order,
            amount=amount,
            payment_method=method,
            reference_number=reference,
            notes=notes,
            recorded_by=recorded_by,
        )

    # ── Summary ───────────────────────────────────────────────────────────────

    def _print_summary(self):
        self.stdout.write('')
        self.stdout.write('  Summary:')
        self.stdout.write(f'    Product categories : {ProductCategory.objects.count()}')
        self.stdout.write(f'    Products           : {Product.objects.count()}')
        self.stdout.write(f'    Customers          : {Customer.objects.count()}')
        self.stdout.write(f'    Sales orders       : {SalesOrder.objects.count()}')

        for status, label in SalesOrder.STATUS_CHOICES:
            n = SalesOrder.objects.filter(status=status).count()
            if n:
                self.stdout.write(f'      {label:<12} {n}')

        self.stdout.write(f'    Payments           : {Payment.objects.count()}')
        self.stdout.write(f'    Inventory movements: {InventoryMovement.objects.count()}')
