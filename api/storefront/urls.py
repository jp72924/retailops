from django.urls import path

from .views import (
    CategoryTreeView,
    ListingDetailView,
    ListingListView,
    PaymentInstructionsView,
)

app_name = 'storefront'

urlpatterns = [
    path('catalog/listings/',              ListingListView.as_view(),        name='listing-list'),
    path('catalog/listings/<slug:slug>/',  ListingDetailView.as_view(),      name='listing-detail'),
    path('catalog/categories/',            CategoryTreeView.as_view(),       name='category-tree'),
    path('payment-instructions/',          PaymentInstructionsView.as_view(), name='payment-instructions'),
]
