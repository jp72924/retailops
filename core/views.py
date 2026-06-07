import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .decorators import role_required
from .models import (
    Customer, InventoryMovement, Payment,
    Product, ProductCategory, Role,
    SalesOrder, SalesOrderItem, SystemSettings, User,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_line_items(post_data):
    """
    Extract line-item tuples from POST data.
    Returns a list of (product_id_str, quantity_str, unit_price_str).
    Skips index slots where product is absent (handles JS remove-row gaps).
    """
    try:
        count = int(post_data.get('line_item_count', 0))
    except (ValueError, TypeError):
        count = 0
    items = []
    for n in range(1, count + 1):
        product_id = post_data.get(f'product_{n}', '').strip()
        if not product_id:
            continue
        items.append((
            product_id,
            post_data.get(f'quantity_{n}', '').strip(),
            post_data.get(f'unit_price_{n}', '').strip(),
        ))
    return items


def _to_decimal(value, default=Decimal('0.00')):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return default


# ── Auth ──────────────────────────────────────────────────────────────────────

def login_view(request):
    """GET: render login form. POST: authenticate; redirect to dashboard on success."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=email, password=password)
        if user is not None:
            login(request, user)
            return redirect(request.GET.get('next', 'dashboard'))
        error = 'Invalid email or password.'

    return render(request, 'core/login.html', {'error': error})


@require_POST
@login_required
def logout_view(request):
    """POST: clear session and redirect to login."""
    logout(request)
    return redirect('login')


# ── Dashboard ─────────────────────────────────────────────────────────────────

@login_required
def dashboard(request):
    """
    GET: summary cards (orders this month, revenue, pending payments, low-stock count),
    last 5 orders, and low-stock product list.
    """
    now = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    orders_this_month = SalesOrder.objects.filter(created_at__gte=month_start).count()

    revenue_this_month = (
        SalesOrder.objects
        .filter(status__in=[SalesOrder.PAID, SalesOrder.SHIPPED, SalesOrder.DELIVERED],
                paid_at__gte=month_start)
        .aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    )

    pending_payments_count = SalesOrder.objects.filter(status=SalesOrder.CONFIRMED).count()

    # current_stock is a computed property — evaluate in Python after prefetch
    all_products = list(
        Product.objects.prefetch_related('inventory_movements').select_related('category')
    )
    low_stock_products = [p for p in all_products if p.is_low_stock or p.is_out_of_stock]

    recent_orders = (
        SalesOrder.objects
        .select_related('customer', 'created_by')
        .order_by('-created_at')[:5]
    )

    return render(request, 'core/dashboard.html', {
        'stats': {
            'orders_this_month':     orders_this_month,
            'revenue_this_month':    revenue_this_month,
            'pending_payments_count': pending_payments_count,
            'low_stock_count':       len(low_stock_products),
        },
        'recent_orders':      recent_orders,
        'low_stock_products': low_stock_products,
    })


# ── Customers ─────────────────────────────────────────────────────────────────

@login_required
def customer_list(request):
    """GET: paginated customer list with search by name or email."""
    query = request.GET.get('q', '').strip()
    qs = Customer.objects.order_by('first_name', 'last_name')
    if query:
        qs = qs.filter(
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(email__icontains=query)      |
            Q(national_id__icontains=query)|
            Q(phone__icontains=query)
        )
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'core/customer_list.html', {
        'page_obj': page_obj,
        'query':    query,
    })


@login_required
def customer_create(request):
    """
    GET:  blank customer registration form.
    POST: validate and create Customer; redirect to customer-detail on success.
    """
    if request.method == 'POST':
        data = request.POST
        first_name    = data.get('first_name', '').strip()
        last_name     = data.get('last_name', '').strip()
        email         = data.get('email', '').strip()
        phone         = data.get('phone', '').strip()
        national_id   = data.get('national_id', '').strip()
        date_of_birth_raw = data.get('date_of_birth', '').strip()
        gender        = data.get('gender', '').strip()
        address_line1 = data.get('address_line1', '').strip()
        address_line2 = data.get('address_line2', '').strip()
        city          = data.get('city', '').strip()
        state         = data.get('state', '').strip()
        postal_code   = data.get('postal_code', '').strip()
        country       = data.get('country', '').strip()
        notes         = data.get('notes', '').strip()

        errors = {}
        if not first_name:
            errors['first_name'] = 'First name is required.'
        if not last_name:
            errors['last_name'] = 'Last name is required.'
        if not email:
            errors['email'] = 'Email address is required.'
        elif Customer.objects.filter(email=email).exists():
            errors['email'] = 'A customer with this email already exists.'
        if national_id and Customer.objects.filter(national_id=national_id).exists():
            errors['national_id'] = 'This ID number is already registered.'
        if gender and gender not in ('M', 'F'):
            errors['gender'] = 'Invalid gender value.'

        date_of_birth = None
        if date_of_birth_raw:
            try:
                date_of_birth = datetime.date.fromisoformat(date_of_birth_raw)
            except ValueError:
                errors['date_of_birth'] = 'Invalid date format.'

        if not address_line1:
            errors['address_line1'] = 'Address is required.'
        if not city:
            errors['city'] = 'City is required.'
        if not state:
            errors['state'] = 'State is required.'
        if not postal_code:
            errors['postal_code'] = 'Postal code is required.'
        if not country:
            errors['country'] = 'Country is required.'

        form_data = {
            'first_name': first_name, 'last_name': last_name, 'email': email,
            'phone': phone, 'national_id': national_id,
            'date_of_birth': date_of_birth_raw, 'gender': gender,
            'address_line1': address_line1, 'address_line2': address_line2,
            'city': city, 'state': state, 'postal_code': postal_code,
            'country': country, 'notes': notes,
        }

        if errors:
            return render(request, 'core/customer_form.html', {'errors': errors, 'form_data': form_data})

        customer = Customer.objects.create(
            first_name=first_name, last_name=last_name, email=email,
            phone=phone, national_id=national_id or None,
            date_of_birth=date_of_birth, gender=gender,
            address_line1=address_line1, address_line2=address_line2,
            city=city, state=state, postal_code=postal_code,
            country=country, notes=notes,
        )
        messages.success(request, f'Customer "{customer.get_full_name()}" registered.')
        return redirect('customer-detail', pk=customer.pk)

    return render(request, 'core/customer_form.html', {'errors': {}, 'form_data': {}})


@login_required
def customer_detail(request, pk):
    """GET: read-only customer summary with order history."""
    customer = get_object_or_404(Customer, pk=pk)
    orders = customer.orders.order_by('-created_at')
    return render(request, 'core/customer_detail.html', {
        'customer': customer,
        'orders':   orders,
    })


@login_required
def customer_edit(request, pk):
    """
    GET:  pre-filled edit form.
    POST: validate and update Customer; redirect to customer-detail on success.
    """
    customer = get_object_or_404(Customer, pk=pk)

    if request.method == 'POST':
        data = request.POST
        first_name    = data.get('first_name', '').strip()
        last_name     = data.get('last_name', '').strip()
        email         = data.get('email', '').strip()
        phone         = data.get('phone', '').strip()
        national_id   = data.get('national_id', '').strip()
        date_of_birth_raw = data.get('date_of_birth', '').strip()
        gender        = data.get('gender', '').strip()
        address_line1 = data.get('address_line1', '').strip()
        address_line2 = data.get('address_line2', '').strip()
        city          = data.get('city', '').strip()
        state         = data.get('state', '').strip()
        postal_code   = data.get('postal_code', '').strip()
        country       = data.get('country', '').strip()
        notes         = data.get('notes', '').strip()

        errors = {}
        if not first_name:
            errors['first_name'] = 'First name is required.'
        if not last_name:
            errors['last_name'] = 'Last name is required.'
        if not email:
            errors['email'] = 'Email address is required.'
        elif Customer.objects.filter(email=email).exclude(pk=customer.pk).exists():
            errors['email'] = 'A customer with this email already exists.'
        if national_id and Customer.objects.filter(national_id=national_id).exclude(pk=customer.pk).exists():
            errors['national_id'] = 'This ID number is already registered.'
        if gender and gender not in ('M', 'F'):
            errors['gender'] = 'Invalid gender value.'

        date_of_birth = None
        if date_of_birth_raw:
            try:
                date_of_birth = datetime.date.fromisoformat(date_of_birth_raw)
            except ValueError:
                errors['date_of_birth'] = 'Invalid date format.'

        if not address_line1:
            errors['address_line1'] = 'Address is required.'
        if not city:
            errors['city'] = 'City is required.'
        if not state:
            errors['state'] = 'State is required.'
        if not postal_code:
            errors['postal_code'] = 'Postal code is required.'
        if not country:
            errors['country'] = 'Country is required.'

        form_data = {
            'first_name': first_name, 'last_name': last_name, 'email': email,
            'phone': phone, 'national_id': national_id,
            'date_of_birth': date_of_birth_raw, 'gender': gender,
            'address_line1': address_line1, 'address_line2': address_line2,
            'city': city, 'state': state, 'postal_code': postal_code,
            'country': country, 'notes': notes,
        }

        if errors:
            return render(request, 'core/customer_form.html',
                          {'customer': customer, 'errors': errors, 'form_data': form_data})

        customer.first_name    = first_name
        customer.last_name     = last_name
        customer.email         = email
        customer.phone         = phone
        customer.national_id   = national_id or None
        customer.date_of_birth = date_of_birth
        customer.gender        = gender
        customer.address_line1 = address_line1
        customer.address_line2 = address_line2
        customer.city          = city
        customer.state         = state
        customer.postal_code   = postal_code
        customer.country       = country
        customer.notes         = notes
        customer.save()

        messages.success(request, f'Customer "{customer.get_full_name()}" updated.')
        return redirect('customer-detail', pk=customer.pk)

    form_data = {
        'first_name': customer.first_name,
        'last_name':  customer.last_name,
        'email':      customer.email,
        'phone':      customer.phone,
        'national_id': customer.national_id or '',
        'date_of_birth': customer.date_of_birth.isoformat() if customer.date_of_birth else '',
        'gender':     customer.gender,
        'address_line1': customer.address_line1,
        'address_line2': customer.address_line2,
        'city':       customer.city,
        'state':      customer.state,
        'postal_code': customer.postal_code,
        'country':    customer.country,
        'notes':      customer.notes,
    }
    return render(request, 'core/customer_form.html',
                  {'customer': customer, 'errors': {}, 'form_data': form_data})


@require_POST
@login_required
def customer_delete(request, pk):
    """POST: delete customer (only if no associated orders); redirect to customer-list."""
    customer = get_object_or_404(Customer, pk=pk)
    customer.delete()
    messages.success(request, f'Customer "{customer.get_full_name()}" deleted.')
    return redirect('customer-list')


# ── Sales Orders ──────────────────────────────────────────────────────────────

@login_required
def order_list(request):
    """
    GET: filterable, paginated order list.
    Query params: q (order# or customer name), status, date_from, date_to.
    """
    query         = request.GET.get('q', '').strip()
    status_filter = request.GET.get('status', '').strip()
    date_from     = request.GET.get('date_from', '').strip()
    date_to       = request.GET.get('date_to', '').strip()

    qs = SalesOrder.objects.select_related('customer').order_by('-created_at')
    if query:
        qs = qs.filter(
            Q(order_number__icontains=query) |
            Q(customer__first_name__icontains=query) |
            Q(customer__last_name__icontains=query)
        )
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))
    return render(request, 'core/order_list.html', {
        'page_obj':      page_obj,
        'query':         query,
        'status_filter': status_filter,
        'date_from':     date_from,
        'date_to':       date_to,
        'status_choices': SalesOrder.STATUS_CHOICES,
    })


def _order_form_context(order=None):
    """Build common context dict for the order create/edit form."""
    sys_settings = SystemSettings.get()
    return {
        'order': order,
        'customers': Customer.objects.order_by('first_name', 'last_name'),
        'products': Product.objects.filter(is_active=True).order_by('name'),
        'payment_method_choices': Payment.METHOD_CHOICES,
        'currency_symbol': sys_settings.currency_symbol,
        'currency_decimals': sys_settings.decimal_places,
        'secondary_currency_enabled': sys_settings.secondary_currency_enabled,
        'secondary_currency_symbol': sys_settings.secondary_currency_symbol,
        'secondary_decimal_places': sys_settings.secondary_decimal_places,
        'secondary_exchange_rate': sys_settings.secondary_exchange_rate,
    }


def _save_order_items(order, raw_items):
    """
    Replace all line items on `order` with `raw_items`.
    `raw_items` is a list of (product_id_str, quantity_str, unit_price_str).
    Returns (items_saved, error_message_or_None).
    """
    if not raw_items:
        return 0, 'At least one line item is required.'

    order.items.all().delete()
    subtotal = Decimal('0.00')
    for product_id, qty_str, price_str in raw_items:
        try:
            product = Product.objects.get(pk=int(product_id))
        except (Product.DoesNotExist, ValueError):
            return 0, f'Product with id {product_id} not found.'
        quantity = max(1, int(qty_str)) if qty_str.isdigit() else 1
        unit_price = _to_decimal(price_str, product.unit_price)
        item = SalesOrderItem(
            sales_order=order,
            product=product,
            quantity=quantity,
            unit_price=unit_price,
        )
        item.save()  # line_total computed in save()
        subtotal += item.line_total

    discount = order.discount_amount
    order.subtotal      = subtotal
    order.total_amount  = max(Decimal('0.00'), subtotal + order.tax_amount - discount)
    order.save()
    return len(raw_items), None


@role_required('Staff', 'Manager', 'Admin')
def order_create(request):
    """
    GET:  blank order form with customer selector and empty line-items table.
    POST: create SalesOrder (status=Draft) with SalesOrderItems;
          redirect to order-detail on success.
    Allowed roles: Staff, Manager, Admin.
    """
    if request.method == 'POST':
        data        = request.POST
        customer_id = data.get('customer', '').strip()
        discount    = _to_decimal(data.get('discount_amount', '0'))
        notes       = data.get('notes', '').strip()
        raw_items   = _parse_line_items(data)

        errors = {}
        customer = None
        if not customer_id:
            errors['customer'] = 'Please select a customer.'
        else:
            try:
                customer = Customer.objects.get(pk=int(customer_id))
            except (Customer.DoesNotExist, ValueError):
                errors['customer'] = 'Invalid customer selected.'

        if not raw_items:
            errors['line_items'] = 'At least one line item is required.'

        if errors:
            ctx = _order_form_context()
            ctx['errors'] = errors
            messages.error(request, 'Please correct the errors below.')
            return render(request, 'core/order_detail.html', ctx)

        with transaction.atomic():
            order = SalesOrder.objects.create(
                customer=customer,
                status=SalesOrder.DRAFT,
                discount_amount=discount,
                notes=notes,
                created_by=request.user,
            )
            _, err = _save_order_items(order, raw_items)
            if err:
                messages.error(request, err)
                ctx = _order_form_context()
                ctx['errors'] = {'line_items': err}
                return render(request, 'core/order_detail.html', ctx)

        messages.success(request, f'Order {order.order_number} created.')
        return redirect('order-detail', pk=order.pk)

    return render(request, 'core/order_detail.html', _order_form_context())


@role_required('Staff', 'Manager', 'Admin')
def order_detail(request, pk):
    """
    GET:  order header, customer panel, line items, totals, payment history,
          and role-gated action buttons.
    POST: update editable fields (line items, notes, discount) while the order
          is in Draft or Pending status.
    Allowed roles: Staff, Manager, Admin.
    """
    order = get_object_or_404(SalesOrder, pk=pk)

    if request.method == 'POST':
        if order.status not in (SalesOrder.DRAFT, SalesOrder.PENDING):
            messages.error(request, 'Only Draft or Pending orders can be edited.')
            return redirect('order-detail', pk=pk)

        data     = request.POST
        discount = _to_decimal(data.get('discount_amount', '0'))
        notes    = data.get('notes', '').strip()

        # Allow customer re-selection while in Draft
        if order.status == SalesOrder.DRAFT:
            customer_id = data.get('customer', '').strip()
            if customer_id:
                try:
                    order.customer = Customer.objects.get(pk=int(customer_id))
                except (Customer.DoesNotExist, ValueError):
                    messages.error(request, 'Invalid customer selected.')
                    return redirect('order-detail', pk=pk)

        raw_items = _parse_line_items(data)
        if not raw_items:
            messages.error(request, 'At least one line item is required.')
            return redirect('order-detail', pk=pk)

        with transaction.atomic():
            order.discount_amount = discount
            order.notes           = notes
            order.save()
            _, err = _save_order_items(order, raw_items)
            if err:
                messages.error(request, err)
                return redirect('order-detail', pk=pk)

        messages.success(request, f'Order {order.order_number} updated.')
        return redirect('order-detail', pk=pk)

    ctx = _order_form_context(order)
    return render(request, 'core/order_detail.html', ctx)


@require_POST
@role_required('Staff', 'Manager', 'Admin')
def order_delete(request, pk):
    """POST: delete order (Draft status only); redirect to order-list. Allowed roles: Staff, Manager, Admin."""
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.DRAFT)
    order.delete()
    messages.success(request, f'Order {order.order_number} deleted.')
    return redirect('order-list')


# ── Order Status Transitions ──────────────────────────────────────────────────

@require_POST
@role_required('Staff', 'Manager', 'Admin')
def order_submit(request, pk):
    """
    POST: Draft → Pending.
    Allowed roles: Staff, Manager, Admin.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.DRAFT)

    if not order.items.exists():
        messages.error(request, 'Cannot submit an order with no line items.')
        return redirect('order-detail', pk=pk)

    order.status = SalesOrder.PENDING
    order.save()
    messages.success(request, f'{order.order_number} submitted for review.')
    return redirect('order-detail', pk=pk)


@require_POST
@role_required('Manager', 'Admin')
def order_confirm(request, pk):
    """
    POST: Pending → Confirmed.
    Allowed roles: Manager, Admin.
    Side-effect: creates InventoryMovement records deducting reserved stock for each line item.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.PENDING)

    with transaction.atomic():
        order.status       = SalesOrder.CONFIRMED
        order.confirmed_by = request.user
        order.confirmed_at = timezone.now()
        order.save()

        for item in order.items.select_related('product').all():
            InventoryMovement.objects.create(
                product=item.product,
                movement_type=InventoryMovement.SALE,
                quantity=-item.quantity,
                reference_type=InventoryMovement.SALES_ORDER,
                reference_id=order.pk,
                notes=f'Stock deducted on confirmation of {order.order_number}',
                created_by=request.user,
            )

    messages.success(request, f'{order.order_number} confirmed.')
    return redirect('order-detail', pk=pk)


@require_POST
@role_required('Staff', 'Manager', 'Admin')
def order_ship(request, pk):
    """
    POST: Paid → Shipped.
    Allowed roles: Staff, Manager, Admin.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.PAID)
    order.status = SalesOrder.SHIPPED
    order.save()
    messages.success(request, f'{order.order_number} marked as shipped.')
    return redirect('order-detail', pk=pk)


@require_POST
@role_required('Staff', 'Manager', 'Admin')
def order_deliver(request, pk):
    """
    POST: Shipped → Delivered.
    Allowed roles: Staff, Manager, Admin.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.SHIPPED)
    order.status = SalesOrder.DELIVERED
    order.save()
    messages.success(request, f'{order.order_number} marked as delivered.')
    return redirect('order-detail', pk=pk)


@require_POST
@role_required('Manager', 'Admin')
def order_cancel(request, pk):
    """
    POST: Confirmed → Cancelled.
    Allowed roles: Manager, Admin.
    Side-effect: creates InventoryMovement records restoring reserved stock for each line item.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.CONFIRMED)

    with transaction.atomic():
        order.status = SalesOrder.CANCELLED
        order.save()

        for item in order.items.select_related('product').all():
            InventoryMovement.objects.create(
                product=item.product,
                movement_type=InventoryMovement.RETURN,
                quantity=item.quantity,
                reference_type=InventoryMovement.SALES_ORDER,
                reference_id=order.pk,
                notes=f'Stock restored on cancellation of {order.order_number}',
                created_by=request.user,
            )

    messages.success(request, f'{order.order_number} cancelled.')
    return redirect('order-detail', pk=pk)


@require_POST
@role_required('Admin')
def order_refund(request, pk):
    """
    POST: Paid → Refunded.
    Allowed roles: Admin only.
    Side-effect: creates InventoryMovement records adding stock back per line item.
    """
    order = get_object_or_404(SalesOrder, pk=pk, status=SalesOrder.PAID)

    with transaction.atomic():
        order.status = SalesOrder.REFUNDED
        order.save()

        for item in order.items.select_related('product').all():
            InventoryMovement.objects.create(
                product=item.product,
                movement_type=InventoryMovement.RETURN,
                quantity=item.quantity,
                reference_type=InventoryMovement.SALES_ORDER,
                reference_id=order.pk,
                notes=f'Stock restored on refund of {order.order_number}',
                created_by=request.user,
            )

    messages.success(request, f'{order.order_number} marked as refunded.')
    return redirect('order-detail', pk=pk)


# ── Payments ──────────────────────────────────────────────────────────────────

@login_required
def payment_list(request):
    """GET: paginated payment list with search by payment# or order#."""
    query         = request.GET.get('q', '').strip()
    method_filter = request.GET.get('method', '').strip()
    status_filter = request.GET.get('status', '').strip()
    date_from     = request.GET.get('date_from', '').strip()
    date_to       = request.GET.get('date_to', '').strip()

    qs = (
        Payment.objects
        .select_related('sales_order', 'sales_order__customer', 'recorded_by')
        .order_by('-created_at')
    )
    if query:
        qs = qs.filter(
            Q(payment_number__icontains=query) |
            Q(sales_order__order_number__icontains=query)
        )
    if method_filter:
        qs = qs.filter(payment_method=method_filter)
    if status_filter:
        qs = qs.filter(status=status_filter)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    filter_params = request.GET.copy()
    filter_params.pop('page', None)
    for key in list(filter_params.keys()):
        if not filter_params.get(key):
            filter_params.pop(key, None)
    page_obj = Paginator(qs, 20).get_page(request.GET.get('page'))
    return render(request, 'core/payment_list.html', {
        'page_obj':     page_obj,
        'query':        query,
        'method_filter': method_filter,
        'status_filter': status_filter,
        'date_from':    date_from,
        'date_to':      date_to,
        'method_choices': Payment.METHOD_CHOICES,
        'status_choices': Payment.STATUS_CHOICES,
        'filter_querystring': filter_params.urlencode(),
    })


@require_POST
@login_required
def payment_create(request):
    """
    POST: record a payment from the modal form.
    Automatically transitions the linked SalesOrder to Paid if total_amount is covered.
    Redirects back to order-detail on success.
    """
    order_id = request.POST.get('sales_order', '').strip()
    order    = get_object_or_404(SalesOrder, pk=order_id)

    amount           = _to_decimal(request.POST.get('amount', '0'))
    payment_method   = request.POST.get('payment_method', '').strip()
    reference_number = request.POST.get('reference_number', '').strip()
    notes            = request.POST.get('notes', '').strip()

    valid_methods = [m[0] for m in Payment.METHOD_CHOICES]

    if amount <= Decimal('0'):
        messages.error(request, 'Payment amount must be greater than zero.')
        return redirect('order-detail', pk=order.pk)

    if payment_method not in valid_methods:
        messages.error(request, 'Please select a valid payment method.')
        return redirect('order-detail', pk=order.pk)

    with transaction.atomic():
        payment = Payment.objects.create(
            sales_order=order,
            amount=amount,
            payment_method=payment_method,
            reference_number=reference_number,
            notes=notes,
            recorded_by=request.user,
        )

        # Auto-transition order to Paid when fully covered
        total_paid = (
            order.payments
            .filter(status=Payment.CONFIRMED)
            .aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        )

        if total_paid >= order.total_amount:
            order.status  = SalesOrder.PAID
            order.paid_at = timezone.now()
            order.save()
            messages.success(
                request,
                f'Payment {payment.payment_number} recorded. Order {order.order_number} marked as Paid.'
            )
        else:
            messages.success(
                request,
                f'Payment {payment.payment_number} recorded. '
                f'Outstanding balance: ${order.amount_outstanding}.'
            )

    return redirect('order-detail', pk=order.pk)


@login_required
def payment_detail(request, pk):
    """GET: read-only payment receipt."""
    payment = get_object_or_404(Payment, pk=pk)
    return render(request, 'core/payment_detail.html', {'payment': payment})


# ── Inventory ─────────────────────────────────────────────────────────────────

@login_required
def inventory_list(request):
    """
    GET: product table with current stock levels, low-stock banner, and filter bar.
    Query params: q (SKU or name), category, stock (all|low|out|ok).
    """
    query           = request.GET.get('q', '').strip()
    category_filter = request.GET.get('category', '').strip()
    stock_filter    = request.GET.get('stock', '').strip()

    qs = (
        Product.objects
        .select_related('category')
        .prefetch_related('inventory_movements')
        .order_by('name')
    )
    if query:
        qs = qs.filter(Q(sku__icontains=query) | Q(name__icontains=query))
    if category_filter:
        qs = qs.filter(category_id=category_filter)

    # current_stock is a computed property — evaluate in Python
    products = list(qs)
    if stock_filter == 'out':
        products = [p for p in products if p.is_out_of_stock]
    elif stock_filter == 'low':
        products = [p for p in products if p.is_low_stock]
    elif stock_filter == 'ok':
        products = [p for p in products if not p.is_low_stock and not p.is_out_of_stock]

    # Banner count is always across the full (unfiltered) product set
    if stock_filter:
        all_products = list(
            Product.objects.prefetch_related('inventory_movements')
        )
        low_stock_count = sum(1 for p in all_products if p.is_low_stock or p.is_out_of_stock)
    else:
        low_stock_count = sum(1 for p in products if p.is_low_stock or p.is_out_of_stock)

    return render(request, 'core/inventory_list.html', {
        'products':        products,
        'categories':      ProductCategory.objects.order_by('name'),
        'low_stock_count': low_stock_count,
        'query':           query,
        'category_filter': category_filter,
        'stock_filter':    stock_filter,
    })


def _merge_product_validation_errors(errors, exc):
    if hasattr(exc, 'message_dict'):
        items = exc.message_dict.items()
    else:
        items = [('image', exc.messages)]

    for field, messages_ in items:
        target = field if field in {
            'sku', 'name', 'category', 'unit_of_measure', 'unit_price',
            'low_stock_threshold', 'image', 'external_image_url', 'is_active',
        } else 'image'
        errors[target] = ' '.join(str(message) for message in messages_)


@login_required
@role_required('Manager', 'Admin')
def product_create(request):
    """
    GET:  add product form.
    POST: create Product; redirect to inventory-list on success.
    Allowed roles: Manager, Admin.
    """
    if request.method == 'POST':
        data        = request.POST
        sku         = data.get('sku', '').strip()
        name        = data.get('name', '').strip()
        category_id = data.get('category', '').strip()
        unit_of_measure = data.get('unit_of_measure', '').strip()
        description = data.get('description', '').strip()
        unit_price  = _to_decimal(data.get('unit_price', ''))
        low_stock_threshold = data.get('low_stock_threshold', '10').strip()
        image_upload = request.FILES.get('image')
        external_image_url = data.get('external_image_url', '').strip()
        is_active   = data.get('is_active') == '1'

        errors = {}
        category = None
        if not sku:
            errors['sku'] = 'SKU is required.'
        elif Product.objects.filter(sku=sku).exists():
            errors['sku'] = 'A product with this SKU already exists.'
        if not name:
            errors['name'] = 'Product name is required.'
        if not category_id:
            errors['category'] = 'Please select a category.'
        else:
            try:
                category = ProductCategory.objects.get(pk=int(category_id))
            except (ProductCategory.DoesNotExist, ValueError):
                errors['category'] = 'Please select a valid category.'
        valid_units = [u[0] for u in Product.UNIT_CHOICES]
        if unit_of_measure not in valid_units:
            errors['unit_of_measure'] = 'Please select a valid unit of measure.'
        if unit_price <= Decimal('0'):
            errors['unit_price'] = 'Unit price must be greater than zero.'
        try:
            threshold = int(low_stock_threshold)
            if threshold < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors['low_stock_threshold'] = 'Threshold must be a non-negative whole number.'
            threshold = 10

        form_data = {
            'sku': sku, 'name': name, 'category': category_id,
            'unit_of_measure': unit_of_measure, 'description': description,
            'unit_price': str(unit_price), 'low_stock_threshold': low_stock_threshold,
            'external_image_url': external_image_url,
            'is_active': '1' if is_active else '',
        }

        product = None
        if not errors:
            product = Product(
                sku=sku, name=name, category=category,
                unit_of_measure=unit_of_measure, description=description,
                unit_price=unit_price, low_stock_threshold=threshold,
                image=image_upload, external_image_url=external_image_url,
                is_active=is_active,
            )
            try:
                product.full_clean()
            except ValidationError as exc:
                _merge_product_validation_errors(errors, exc)

        if errors:
            return render(request, 'core/product_form.html', {
                'product': None,
                'categories': ProductCategory.objects.all(),
                'unit_choices': Product.UNIT_CHOICES,
                'errors': errors,
                'form_data': form_data,
            })

        product.save()
        messages.success(request, f'Product "{product.name}" added.')
        return redirect('inventory-list')

    return render(request, 'core/product_form.html', {
        'product': None,
        'categories': ProductCategory.objects.all(),
        'unit_choices': Product.UNIT_CHOICES,
        'errors': {},
        'form_data': {},
    })


@login_required
@role_required('Manager', 'Admin')
def product_edit(request, pk):
    """
    GET:  pre-filled product edit form.
    POST: update Product; redirect to inventory-list on success.
    Allowed roles: Manager, Admin.
    """
    product = get_object_or_404(Product, pk=pk)

    if request.method == 'POST':
        data        = request.POST
        sku         = data.get('sku', '').strip()
        name        = data.get('name', '').strip()
        category_id = data.get('category', '').strip()
        unit_of_measure = data.get('unit_of_measure', '').strip()
        description = data.get('description', '').strip()
        unit_price  = _to_decimal(data.get('unit_price', ''))
        low_stock_threshold = data.get('low_stock_threshold', '10').strip()
        image_upload = request.FILES.get('image')
        external_image_url = data.get('external_image_url', '').strip()
        remove_image = data.get('remove_image') == '1'
        is_active   = data.get('is_active') == '1'

        errors = {}
        category = None
        if not sku:
            errors['sku'] = 'SKU is required.'
        elif Product.objects.filter(sku=sku).exclude(pk=product.pk).exists():
            errors['sku'] = 'A product with this SKU already exists.'
        if not name:
            errors['name'] = 'Product name is required.'
        if not category_id:
            errors['category'] = 'Please select a category.'
        else:
            try:
                category = ProductCategory.objects.get(pk=int(category_id))
            except (ProductCategory.DoesNotExist, ValueError):
                errors['category'] = 'Please select a valid category.'
        valid_units = [u[0] for u in Product.UNIT_CHOICES]
        if unit_of_measure not in valid_units:
            errors['unit_of_measure'] = 'Please select a valid unit of measure.'
        if unit_price <= Decimal('0'):
            errors['unit_price'] = 'Unit price must be greater than zero.'
        try:
            threshold = int(low_stock_threshold)
            if threshold < 0:
                raise ValueError
        except (ValueError, TypeError):
            errors['low_stock_threshold'] = 'Threshold must be a non-negative whole number.'
            threshold = product.low_stock_threshold

        form_data = {
            'sku': sku, 'name': name, 'category': category_id,
            'unit_of_measure': unit_of_measure, 'description': description,
            'unit_price': str(unit_price), 'low_stock_threshold': low_stock_threshold,
            'external_image_url': external_image_url,
            'is_active': '1' if is_active else '',
        }

        old_image_name = product.image.name if product.image else ''
        old_image_storage = product.image.storage if product.image else None

        if not errors:
            product.sku               = sku
            product.name              = name
            product.category          = category
            product.unit_of_measure   = unit_of_measure
            product.description       = description
            product.unit_price        = unit_price
            product.low_stock_threshold = threshold
            product.external_image_url = external_image_url
            if remove_image:
                product.image = None
            elif image_upload:
                product.image = image_upload
            product.is_active         = is_active
            try:
                product.full_clean(validate_unique=False)
            except ValidationError as exc:
                _merge_product_validation_errors(errors, exc)

        if errors:
            return render(request, 'core/product_form.html', {
                'product': get_object_or_404(Product, pk=pk),
                'categories': ProductCategory.objects.all(),
                'unit_choices': Product.UNIT_CHOICES,
                'errors': errors,
                'form_data': form_data,
            })

        product.save()
        if old_image_name and old_image_storage and (remove_image or image_upload):
            if old_image_name != (product.image.name if product.image else ''):
                old_image_storage.delete(old_image_name)

        messages.success(request, f'Product "{product.name}" updated.')
        return redirect('inventory-list')

    return render(request, 'core/product_form.html', {
        'product': product,
        'categories': ProductCategory.objects.all(),
        'unit_choices': Product.UNIT_CHOICES,
        'errors': {},
        'form_data': {
            'sku':                 product.sku,
            'name':                product.name,
            'category':            str(product.category_id),
            'unit_of_measure':     product.unit_of_measure,
            'description':         product.description or '',
            'unit_price':          str(product.unit_price),
            'low_stock_threshold': str(product.low_stock_threshold),
            'external_image_url':  product.external_image_url or '',
            'is_active':           '1' if product.is_active else '',
        },
    })


@login_required
def product_movements(request, pk):
    """
    GET: returns JSON movement history for a product — consumed by the
    inventory slide-in panel via fetch().

    Response shape:
        {
          "product": {"sku": str, "name": str, "current_stock": int},
          "movements": [
            {"date": str, "type": str, "quantity": int,
             "reference_type": str, "reference_id": int,
             "notes": str, "created_by": str},
            ...
          ]
        }
    """
    product = get_object_or_404(Product, pk=pk)
    movements = (
        product.inventory_movements
        .select_related('created_by')
        .order_by('-created_at')[:50]
    )
    data = {
        'product': {
            'sku': product.sku,
            'name': product.name,
            'current_stock': product.current_stock,
        },
        'movements': [
            {
                'date': m.created_at.isoformat(),
                'type': m.get_movement_type_display(),
                'quantity': m.quantity,
                'reference_type': m.reference_type,
                'reference_id': m.reference_id,
                'notes': m.notes,
                'created_by': m.created_by.get_full_name(),
            }
            for m in movements
        ],
    }
    return JsonResponse(data)


@require_POST
@login_required
@role_required('Manager', 'Admin')
def category_create_ajax(request):
    """
    POST: create a ProductCategory inline from the product form modal.
    Returns JSON — not a page redirect.

    POST fields:
        name        — required, must be unique
        description — optional
        parent_id   — optional FK to existing ProductCategory

    Success: {"ok": true, "id": <pk>, "name": <name>, "display_name": <str>}
    Error:   {"ok": false, "error": "<message>"}, status 400
    """
    name        = request.POST.get('name', '').strip()
    description = request.POST.get('description', '').strip()
    parent_id   = request.POST.get('parent_id', '').strip()

    if not name:
        return JsonResponse({'ok': False, 'error': 'El nombre es obligatorio.'}, status=400)

    if ProductCategory.objects.filter(name=name).exists():
        return JsonResponse(
            {'ok': False, 'error': f'Ya existe una categoría llamada "{name}".'},
            status=400,
        )

    parent = None
    if parent_id:
        try:
            parent = ProductCategory.objects.get(pk=int(parent_id))
        except (ProductCategory.DoesNotExist, ValueError):
            return JsonResponse({'ok': False, 'error': 'Categoría padre no válida.'}, status=400)

    cat = ProductCategory.objects.create(
        name=name,
        description=description,
        parent_category=parent,
    )
    return JsonResponse({
        'ok': True,
        'id': cat.pk,
        'name': cat.name,
        'display_name': str(cat),
    })


# ── Product Categories (full CRUD) ───────────────────────────────────────────


def _is_descendant(ancestor, candidate):
    """
    Return True if `candidate` is a descendant of `ancestor`.
    Uses iterative BFS to avoid recursion limits on deep trees.
    Used to prevent circular parent assignments in category_edit.
    """
    visited = set()
    queue = list(ancestor.subcategories.values_list('pk', flat=True))
    while queue:
        pk = queue.pop(0)
        if pk in visited:
            continue
        visited.add(pk)
        if pk == candidate.pk:
            return True
        queue.extend(
            ProductCategory.objects.filter(parent_category_id=pk)
                           .values_list('pk', flat=True)
        )
    return False


@login_required
def category_list(request):
    q = request.GET.get('q', '').strip()
    qs = (
        ProductCategory.objects
        .select_related('parent_category')
        .annotate(product_count=Count('products'))
        .order_by('name')
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    page_obj = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'core/category_list.html', {'page_obj': page_obj, 'query': q})


@login_required
@role_required('Manager', 'Admin')
def category_create(request):
    all_cats = ProductCategory.objects.order_by('name')
    if request.method == 'POST':
        name        = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        parent_id   = request.POST.get('parent_category', '').strip()
        errors, parent = {}, None

        if not name:
            errors['name'] = 'Name is required.'
        elif len(name) > 150:
            errors['name'] = 'Name must be 150 characters or fewer.'
        elif ProductCategory.objects.filter(name=name).exists():
            errors['name'] = 'A category with this name already exists.'

        if parent_id:
            try:
                parent = ProductCategory.objects.get(pk=int(parent_id))
            except (ProductCategory.DoesNotExist, ValueError):
                errors['parent_category'] = 'Selected parent does not exist.'

        if not errors:
            cat = ProductCategory.objects.create(
                name=name, description=description, parent_category=parent,
            )
            messages.success(request, f'Category "{cat.name}" created.')
            return redirect('category-list')

        form_data = {'name': name, 'description': description, 'parent_category': parent_id}
        return render(request, 'core/category_form.html',
                      {'errors': errors, 'form_data': form_data, 'all_categories': all_cats})

    return render(request, 'core/category_form.html',
                  {'errors': {}, 'form_data': {}, 'all_categories': all_cats})


@login_required
@role_required('Manager', 'Admin')
def category_edit(request, pk):
    category = get_object_or_404(ProductCategory, pk=pk)
    all_cats = ProductCategory.objects.order_by('name')

    if request.method == 'POST':
        name        = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        parent_id   = request.POST.get('parent_category', '').strip()
        errors, parent = {}, None

        if not name:
            errors['name'] = 'Name is required.'
        elif len(name) > 150:
            errors['name'] = 'Name must be 150 characters or fewer.'
        elif ProductCategory.objects.filter(name=name).exclude(pk=category.pk).exists():
            errors['name'] = 'A category with this name already exists.'

        if parent_id:
            try:
                parent_obj = ProductCategory.objects.get(pk=int(parent_id))
                if parent_obj.pk == category.pk:
                    errors['parent_category'] = 'A category cannot be its own parent.'
                elif _is_descendant(category, parent_obj):
                    errors['parent_category'] = 'This would create a circular reference.'
                else:
                    parent = parent_obj
            except (ProductCategory.DoesNotExist, ValueError):
                errors['parent_category'] = 'Selected parent does not exist.'

        if not errors:
            category.name             = name
            category.description      = description
            category.parent_category  = parent
            category.save()
            messages.success(request, f'Category "{category.name}" updated.')
            return redirect('category-list')

        form_data = {'name': name, 'description': description, 'parent_category': parent_id}
        return render(request, 'core/category_form.html',
                      {'category': category, 'errors': errors,
                       'form_data': form_data, 'all_categories': all_cats})

    form_data = {
        'name':            category.name,
        'description':     category.description,
        'parent_category': str(category.parent_category_id) if category.parent_category_id else '',
    }
    return render(request, 'core/category_form.html',
                  {'category': category, 'errors': {}, 'form_data': form_data, 'all_categories': all_cats})


@require_POST
@login_required
@role_required('Manager', 'Admin')
def category_delete(request, pk):
    category = get_object_or_404(ProductCategory, pk=pk)
    name = category.name
    try:
        category.delete()
        messages.success(request, f'Category "{name}" deleted.')
    except ProtectedError:
        messages.error(
            request,
            f'Cannot delete "{name}" — it is assigned to one or more products. '
            f'Reassign or delete those products first.',
        )
    return redirect('category-list')


@require_POST
@role_required('Manager', 'Admin')
def inventory_adjust(request):
    """
    POST: record a manual stock adjustment (purchase, adjustment, or return).
    Allowed roles: Manager, Admin.

    POST fields:
        product_id    — pk of the product to adjust
        movement_type — 'purchase' | 'adjustment' | 'return'
        quantity      — non-zero integer (positive = add, negative = deduct)
        notes         — optional free-text note

    Creates an InventoryMovement with reference_type='ManualAdjustment' and
    reference_id=0 (no linked document).
    """
    data          = request.POST
    product_id    = data.get('product_id', '').strip()
    movement_type = data.get('movement_type', '').strip()
    quantity_raw  = data.get('quantity', '').strip()
    notes         = data.get('notes', '').strip()

    # ── Validate ──────────────────────────────────────────────────────────────
    valid_types = {
        InventoryMovement.PURCHASE,
        InventoryMovement.ADJUSTMENT,
        InventoryMovement.RETURN,
    }

    error = None
    if not product_id:
        error = 'Product is required.'
    elif movement_type not in valid_types:
        error = 'Invalid movement type.'
    else:
        try:
            quantity = int(quantity_raw)
            if quantity == 0:
                error = 'Quantity must be non-zero.'
        except (ValueError, TypeError):
            error = 'Quantity must be a whole number.'

    if error:
        messages.error(request, error)
        return redirect('inventory-list')

    product = get_object_or_404(Product, pk=int(product_id))

    InventoryMovement.objects.create(
        product=product,
        movement_type=movement_type,
        quantity=quantity,
        reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
        reference_id=0,
        notes=notes,
        created_by=request.user,
    )

    sign   = '+' if quantity > 0 else ''
    label  = dict(InventoryMovement.MOVEMENT_TYPE_CHOICES).get(movement_type, movement_type)
    messages.success(
        request,
        f'{label} of {sign}{quantity} recorded for {product.sku} — {product.name}.'
    )
    return redirect('inventory-list')


# ── Users / Staff Management (Admin only) ─────────────────────────────────────

@role_required('Admin')
def user_list(request):
    """GET: user table with role badges, status, and last login. Admin only."""
    return render(request, 'core/user_list.html', {
        'users': User.objects.select_related('role').order_by('first_name').all(),
        'roles': Role.objects.all(),
    })


@require_POST
@role_required('Admin')
def user_invite(request):
    """
    POST: create a new User (is_active=True) and assign a Role.
    Redirects to user-list. Admin only.
    """
    data       = request.POST
    first_name = data.get('first_name', '').strip()
    last_name  = data.get('last_name', '').strip()
    email      = data.get('email', '').strip()
    role_id    = data.get('role', '').strip()
    password   = data.get('password', '')

    errors = []
    if not first_name:
        errors.append('First name is required.')
    if not last_name:
        errors.append('Last name is required.')
    if not email:
        errors.append('Email address is required.')
    elif User.objects.filter(email=email).exists():
        errors.append('A user with this email already exists.')
    if not role_id:
        errors.append('Please select a role.')
    if not password:
        errors.append('A temporary password is required.')

    if errors:
        for msg in errors:
            messages.error(request, msg)
        return redirect('user-list')

    role = get_object_or_404(Role, pk=int(role_id))
    user = User.objects.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        role=role,
    )
    messages.success(request, f'User "{user.get_full_name()}" created with role {role.name}.')
    return redirect('user-list')


@role_required('Admin')
def user_edit(request, pk):
    """
    GET:  pre-filled user edit form (name, role).
    POST (default):         update first_name, last_name, email, role, is_active.
    POST (action=change_password): set a new password.
    Redirects to user-list on success. Admin only.
    """
    edited_user = get_object_or_404(User, pk=pk)

    if request.method == 'POST':
        action = request.POST.get('action', '')

        if action == 'change_password':
            new_password     = request.POST.get('new_password', '')
            confirm_password = request.POST.get('confirm_password', '')
            errors = {}
            if not new_password:
                errors['new_password'] = 'New password is required.'
            elif len(new_password) < 8:
                errors['new_password'] = 'Password must be at least 8 characters.'
            if new_password and new_password != confirm_password:
                errors['confirm_password'] = 'Passwords do not match.'

            if errors:
                return render(request, 'core/user_form.html', {
                    'edited_user': edited_user,
                    'roles': Role.objects.all(),
                    'errors': errors,
                    'form_data': {},
                })

            edited_user.set_password(new_password)
            edited_user.save()
            messages.success(request, f'Password updated for {edited_user.get_full_name()}.')
            return redirect('user-list')

        # Default: update profile
        first_name = request.POST.get('first_name', '').strip()
        last_name  = request.POST.get('last_name', '').strip()
        email      = request.POST.get('email', '').strip()
        role_id    = request.POST.get('role', '').strip()
        is_active  = request.POST.get('is_active') == '1'

        errors = {}
        if not first_name:
            errors['first_name'] = 'First name is required.'
        if not last_name:
            errors['last_name'] = 'Last name is required.'
        if not email:
            errors['email'] = 'Email address is required.'
        elif User.objects.filter(email=email).exclude(pk=edited_user.pk).exists():
            errors['email'] = 'A user with this email already exists.'
        if not role_id:
            errors['role'] = 'Please select a role.'

        form_data = {
            'first_name': first_name, 'last_name': last_name,
            'email': email, 'role': role_id,
        }

        if errors:
            return render(request, 'core/user_form.html', {
                'edited_user': edited_user,
                'roles': Role.objects.all(),
                'errors': errors,
                'form_data': form_data,
            })

        role = get_object_or_404(Role, pk=int(role_id))
        edited_user.first_name = first_name
        edited_user.last_name  = last_name
        edited_user.email      = email
        edited_user.role       = role
        # Prevent admin from deactivating their own account via this form
        if edited_user != request.user:
            edited_user.is_active = is_active
        edited_user.save()

        messages.success(request, f'User "{edited_user.get_full_name()}" updated.')
        return redirect('user-list')

    return render(request, 'core/user_form.html', {
        'edited_user': edited_user,
        'roles': Role.objects.all(),
        'errors': {},
        'form_data': {},
    })


@require_POST
@role_required('Admin')
def user_deactivate(request, pk):
    """POST: set User.is_active=False. Admin only."""
    user = get_object_or_404(User, pk=pk)
    if user == request.user:
        messages.error(request, 'You cannot deactivate your own account.')
        return redirect('user-list')
    user.is_active = False
    user.save()
    messages.success(request, f'{user.get_full_name()} deactivated.')
    return redirect('user-list')


@require_POST
@role_required('Admin')
def user_reactivate(request, pk):
    """POST: set User.is_active=True. Admin only."""
    user = get_object_or_404(User, pk=pk)
    user.is_active = True
    user.save()
    messages.success(request, f'{user.get_full_name()} reactivated.')
    return redirect('user-list')


# ── Regional Settings ──────────────────────────────────────────────────────────

@login_required
def user_settings(request):
    """
    GET:  render personal settings form (timezone, language) + system currency (Admin only).
    POST: save timezone and language for the current user;
          if Admin, also save SystemSettings currency fields.
    """
    from zoneinfo import available_timezones, ZoneInfoNotFoundError, ZoneInfo

    LANGUAGES = [
        ('en', 'English'),
        ('es', 'Spanish (Español)'),
    ]
    timezones = sorted(available_timezones())

    sys_settings = SystemSettings.get()

    if request.method == 'POST':
        tz_name = request.POST.get('timezone', 'UTC').strip()
        language = request.POST.get('language', 'en').strip()

        errors = {}
        try:
            ZoneInfo(tz_name)
        except (ZoneInfoNotFoundError, KeyError):
            errors['timezone'] = 'Please select a valid timezone.'
        if language not in dict(LANGUAGES):
            errors['language'] = 'Please select a valid language.'

        if errors:
            return render(request, 'core/settings.html', {
                'timezones': timezones,
                'languages': LANGUAGES,
                'sys_settings': sys_settings,
                'errors': errors,
            })

        request.user.timezone = tz_name
        request.user.language = language
        request.user.save(update_fields=['timezone', 'language'])

        if request.user.role and request.user.role.name == 'Admin':
            currency_code   = request.POST.get('currency_code', '').strip().upper()
            currency_symbol = request.POST.get('currency_symbol', '').strip()
            decimal_places  = request.POST.get('decimal_places', '2').strip()

            if currency_code and len(currency_code) <= 3:
                sys_settings.currency_code = currency_code
            if currency_symbol:
                sys_settings.currency_symbol = currency_symbol
            try:
                dp = int(decimal_places)
                if 0 <= dp <= 4:
                    sys_settings.decimal_places = dp
            except (ValueError, TypeError):
                pass

            sys_settings.secondary_currency_enabled = request.POST.get('secondary_currency_enabled') == 'on'
            sys_settings.secondary_currency_code   = request.POST.get('secondary_currency_code', '').strip().upper()[:3]
            sys_settings.secondary_currency_symbol = request.POST.get('secondary_currency_symbol', '').strip()[:4]
            try:
                sdp = int(request.POST.get('secondary_decimal_places', '2'))
                if 0 <= sdp <= 4:
                    sys_settings.secondary_decimal_places = sdp
            except (ValueError, TypeError):
                pass
            try:
                rate = Decimal(request.POST.get('secondary_exchange_rate', '1'))
                if rate > 0:
                    sys_settings.secondary_exchange_rate = rate
            except (InvalidOperation, TypeError):
                pass

            sys_settings.secondary_rate_auto_update_enabled = (
                request.POST.get('secondary_rate_auto_update_enabled') == 'on'
            )
            sys_settings.secondary_rate_source_url = request.POST.get(
                'secondary_rate_source_url', '').strip()
            sys_settings.secondary_rate_source_field = request.POST.get(
                'secondary_rate_source_field', '').strip()

            sys_settings.ocr_enabled = request.POST.get('ocr_enabled') == 'on'

            ocr_provider = request.POST.get('ocr_provider', 'vepay').strip()
            if ocr_provider == 'vepay':
                sys_settings.ocr_provider = ocr_provider
            else:
                errors['ocr_provider'] = 'Unsupported OCR provider.'

            sys_settings.ocr_base_url = request.POST.get('ocr_base_url', '').strip()

            if request.POST.get('ocr_api_key_action') == 'replace':
                sys_settings.ocr_api_key = request.POST.get('ocr_api_key', '').strip()

            try:
                timeout_seconds = int(request.POST.get('ocr_timeout_seconds', '30'))
                if timeout_seconds > 0:
                    sys_settings.ocr_timeout_seconds = timeout_seconds
                else:
                    errors['ocr_timeout_seconds'] = 'Must be greater than zero.'
            except (ValueError, TypeError):
                errors['ocr_timeout_seconds'] = 'Must be a whole number.'

            try:
                max_file_mb = int(request.POST.get('ocr_max_file_mb', '8'))
                if max_file_mb > 0:
                    sys_settings.ocr_max_file_mb = max_file_mb
                else:
                    errors['ocr_max_file_mb'] = 'Must be greater than zero.'
            except (ValueError, TypeError):
                errors['ocr_max_file_mb'] = 'Must be a whole number.'

            try:
                retention_days = int(request.POST.get('delete_receipt_image_after_days', '90'))
                if retention_days > 0:
                    sys_settings.delete_receipt_image_after_days = retention_days
                else:
                    errors['delete_receipt_image_after_days'] = 'Must be greater than zero.'
            except (ValueError, TypeError):
                errors['delete_receipt_image_after_days'] = 'Must be a whole number.'

            allowed_ocr_methods = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}
            selected_ocr_methods = request.POST.getlist('ocr_enabled_methods')
            unsupported_methods = sorted(set(selected_ocr_methods) - allowed_ocr_methods)
            if unsupported_methods:
                errors['ocr_enabled_methods'] = 'Unsupported OCR payment method selected.'
            else:
                sys_settings.ocr_enabled_methods = selected_ocr_methods

            sys_settings.ocr_strict_amount = request.POST.get('ocr_strict_amount') == 'on'
            sys_settings.ocr_require_complete = request.POST.get('ocr_require_complete') == 'on'
            sys_settings.receipt_image_required_for_receipt_methods = (
                request.POST.get('receipt_image_required_for_receipt_methods') == 'on'
            )

            if errors:
                return render(request, 'core/settings.html', {
                    'timezones': timezones,
                    'languages': LANGUAGES,
                    'sys_settings': sys_settings,
                    'errors': errors,
                })

            try:
                sys_settings.full_clean()
                sys_settings.save()
            except ValidationError as e:
                return render(request, 'core/settings.html', {
                    'timezones': timezones,
                    'languages': LANGUAGES,
                    'sys_settings': sys_settings,
                    'errors': {k: v[0] for k, v in e.message_dict.items()},
                })

        messages.success(request, 'Settings saved.')
        return redirect('settings')

    return render(request, 'core/settings.html', {
        'timezones': timezones,
        'languages': LANGUAGES,
        'sys_settings': sys_settings,
        'errors': {},
    })


@require_POST
@role_required('Admin')
def secondary_rate_refresh(request):
    """POST: fetch the secondary-currency rate from the configured source and
    save it. Admin only. Redirects back to the settings page with a flash."""
    from .services.bcv import BCVRateError, update_secondary_exchange_rate

    try:
        rate = update_secondary_exchange_rate()
    except BCVRateError as exc:
        messages.error(request, f'Rate update failed: {exc.message}')
    else:
        messages.success(request, f'Secondary exchange rate updated to {rate}.')
    return redirect('settings')
