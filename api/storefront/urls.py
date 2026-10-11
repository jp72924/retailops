from django.urls import path

from .views import (
    CategoryTreeView,
    CustomerOrdersView,
    EntitlementDetailView,
    EntitlementRevealView,
    ListingDetailView,
    ListingListView,
    OrderCreateView,
    OrderDetailView,
    OrderPaymentView,
    PaymentInstructionsView,
)

app_name = 'storefront'

urlpatterns = [
    path('catalog/listings/',              ListingListView.as_view(),        name='listing-list'),
    path('catalog/listings/<slug:slug>/',  ListingDetailView.as_view(),      name='listing-detail'),
    path('catalog/categories/',            CategoryTreeView.as_view(),       name='category-tree'),
    path('payment-instructions/',          PaymentInstructionsView.as_view(), name='payment-instructions'),
    path('orders/',                        OrderCreateView.as_view(),        name='order-create'),
    path('orders/<str:order_number>/',     OrderDetailView.as_view(),        name='order-detail'),
    path('orders/<str:order_number>/payments/', OrderPaymentView.as_view(),  name='order-payment'),
    path('customers/<str:external_ref>/orders/', CustomerOrdersView.as_view(), name='customer-orders'),
    path('entitlements/<int:pk>/',         EntitlementDetailView.as_view(),  name='entitlement-detail'),
    path('entitlements/<int:pk>/reveal/',  EntitlementRevealView.as_view(),  name='entitlement-reveal'),
]
