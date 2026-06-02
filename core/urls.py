from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

urlpatterns = [

    # ── Auth ──────────────────────────────────────────────────────────────────
    # GET  /login/   — render login form
    # POST /login/   — authenticate and redirect to dashboard
    path('login/', views.login_view, name='login'),

    # POST /logout/  — clear session and redirect to login
    path('logout/', views.logout_view, name='logout'),

    # ── Password Reset (unauthenticated) ──────────────────────────────────────
    # GET/POST /password-reset/         — submit email, trigger reset email
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='core/password_reset_form.html',
        email_template_name='core/password_reset_email.txt',
        subject_template_name='core/password_reset_subject.txt',
    ), name='password_reset'),

    # GET /password-reset/done/         — "check your email" confirmation page
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='core/password_reset_done.html',
    ), name='password_reset_done'),

    # GET/POST /password-reset/confirm/<uidb64>/<token>/  — set new password
    path('password-reset/confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='core/password_reset_confirm.html',
    ), name='password_reset_confirm'),

    # GET /password-reset/complete/     — success page with link back to login
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='core/password_reset_complete.html',
    ), name='password_reset_complete'),

    # ── Dashboard ─────────────────────────────────────────────────────────────
    # GET /  — summary cards, recent orders, low-stock alerts, quick actions
    path('', views.dashboard, name='dashboard'),

    # ── Customers ─────────────────────────────────────────────────────────────
    # GET  /customers/          — paginated customer list with search
    path('customers/', views.customer_list, name='customer-list'),

    # GET  /customers/new/      — blank registration form
    # POST /customers/new/      — create customer; redirect to customer-detail on success
    path('customers/new/', views.customer_create, name='customer-create'),

    # GET  /customers/<pk>/     — read-only customer summary + order history
    path('customers/<int:pk>/', views.customer_detail, name='customer-detail'),

    # GET  /customers/<pk>/edit/  — pre-filled edit form
    # POST /customers/<pk>/edit/  — update customer; redirect to customer-detail on success
    path('customers/<int:pk>/edit/', views.customer_edit, name='customer-edit'),

    # POST /customers/<pk>/delete/  — soft-delete or hard-delete; redirect to customer-list
    path('customers/<int:pk>/delete/', views.customer_delete, name='customer-delete'),

    # ── Sales Orders ──────────────────────────────────────────────────────────
    # GET  /orders/  — filterable, paginated order list (search, status, date range)
    path('orders/', views.order_list, name='order-list'),

    # GET  /orders/new/  — blank order form (customer selector + empty line-items table)
    # POST /orders/new/  — create order as Draft; redirect to order-detail on success
    path('orders/new/', views.order_create, name='order-create'),

    # GET  /orders/<pk>/  — order detail: header, customer panel, line items,
    #                       totals, payment history, role-gated action buttons
    # POST /orders/<pk>/  — update editable fields (line items, notes, discount)
    #                       while order is in Draft or Pending status
    path('orders/<int:pk>/', views.order_detail, name='order-detail'),

    # POST /orders/<pk>/delete/  — delete Draft orders only; redirect to order-list
    path('orders/<int:pk>/delete/', views.order_delete, name='order-delete'),

    # ── Order Status Transitions (POST only, role-gated) ──────────────────────
    # Staff:   Draft → Pending
    path('orders/<int:pk>/submit/', views.order_submit, name='order-submit'),

    # Manager: Pending → Confirmed  (also deducts reserved stock)
    path('orders/<int:pk>/confirm/', views.order_confirm, name='order-confirm'),

    # Staff:   Paid → Shipped
    path('orders/<int:pk>/ship/', views.order_ship, name='order-ship'),

    # Staff:   Shipped → Delivered
    path('orders/<int:pk>/deliver/', views.order_deliver, name='order-deliver'),

    # Manager or Admin: Confirmed → Cancelled  (also restores reserved stock)
    path('orders/<int:pk>/cancel/', views.order_cancel, name='order-cancel'),

    # Admin only: Paid → Refunded  (also restores stock)
    path('orders/<int:pk>/refund/', views.order_refund, name='order-refund'),

    # ── Payments ──────────────────────────────────────────────────────────────
    # GET  /payments/      — paginated payment list
    path('payments/', views.payment_list, name='payment-list'),

    # POST /payments/new/  — record a payment from the modal form;
    #                        redirects back to order-detail on success
    path('payments/new/', views.payment_create, name='payment-create'),

    # GET  /payments/<pk>/  — read-only payment receipt view
    path('payments/<int:pk>/', views.payment_detail, name='payment-detail'),

    # ── Inventory ─────────────────────────────────────────────────────────────
    # GET /inventory/  — product table with stock levels, low-stock banner,
    #                    filter bar (SKU/name, category, stock status)
    path('inventory/', views.inventory_list, name='inventory-list'),

    # GET  /inventory/products/new/  — add product form
    # POST /inventory/products/new/  — create product; redirect to inventory-list
    path('inventory/products/new/', views.product_create, name='product-create'),

    # GET  /inventory/products/<pk>/edit/  — pre-filled product edit form
    # POST /inventory/products/<pk>/edit/  — update product; redirect to inventory-list
    path('inventory/products/<int:pk>/edit/', views.product_edit, name='product-edit'),

    # GET /inventory/products/<pk>/movements/  — JSON: movement history for slide-in panel
    path('inventory/products/<int:pk>/movements/', views.product_movements, name='product-movements'),

    # POST /inventory/adjust/  — record a manual stock adjustment (Manager/Admin)
    path('inventory/adjust/', views.inventory_adjust, name='inventory-adjust'),

    # POST /inventory/categories/create/  — inline category creation (Manager/Admin, AJAX)
    path('inventory/categories/create/', views.category_create_ajax, name='category-create'),

    # ── Product Categories (full CRUD) ────────────────────────────────────────
    # GET  /inventory/categories/           — paginated category list with search
    path('inventory/categories/', views.category_list, name='category-list'),

    # GET  /inventory/categories/new/       — blank category form
    # POST /inventory/categories/new/       — create; redirect to category-list
    path('inventory/categories/new/', views.category_create, name='category-create-page'),

    # GET  /inventory/categories/<pk>/edit/ — pre-filled edit form
    # POST /inventory/categories/<pk>/edit/ — update; redirect to category-list
    path('inventory/categories/<int:pk>/edit/', views.category_edit, name='category-edit'),

    # POST /inventory/categories/<pk>/delete/ — delete; redirect to category-list
    path('inventory/categories/<int:pk>/delete/', views.category_delete, name='category-delete'),

    # ── Users / Staff Management (Admin only) ─────────────────────────────────
    # GET /users/  — user table with role badges and status
    path('users/', views.user_list, name='user-list'),

    # POST /users/invite/  — create User record and send invite; redirects to user-list
    path('users/invite/', views.user_invite, name='user-invite'),

    # GET  /users/<pk>/edit/  — pre-filled user edit form (name, role)
    # POST /users/<pk>/edit/  — update user; redirect to user-list
    path('users/<int:pk>/edit/', views.user_edit, name='user-edit'),

    # POST /users/<pk>/deactivate/  — set is_active=False; redirect to user-list
    path('users/<int:pk>/deactivate/', views.user_deactivate, name='user-deactivate'),

    # POST /users/<pk>/reactivate/  — set is_active=True; redirect to user-list
    path('users/<int:pk>/reactivate/', views.user_reactivate, name='user-reactivate'),

    # ── Regional Settings ─────────────────────────────────────────────────────
    # GET  /settings/  — personal timezone/language + system currency (Admin)
    # POST /settings/  — save preferences
    path('settings/', views.user_settings, name='settings'),
]
