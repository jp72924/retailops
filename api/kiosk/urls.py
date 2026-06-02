from django.urls import path

from .views import (
    KioskCheckoutView,
    KioskHeartbeatView,
    KioskIdentifyView,
    KioskProductDetailView,
    KioskProductLookupView,
    KioskProductSearchView,
    KioskReceiptView,
    KioskRegisterView,
)

app_name = 'kiosk'

urlpatterns = [
    path('identify/',              KioskIdentifyView.as_view(),      name='identify'),
    path('register/',              KioskRegisterView.as_view(),       name='register'),
    path('products/',              KioskProductSearchView.as_view(),  name='product-search'),
    path('products/<int:pk>/',     KioskProductDetailView.as_view(),  name='product-detail'),
    path('product/<str:sku>/',     KioskProductLookupView.as_view(),  name='product-lookup'),
    path('checkout/',              KioskCheckoutView.as_view(),       name='checkout'),
    path('receipt/<int:order_id>/', KioskReceiptView.as_view(),       name='receipt'),
    path('heartbeat/',             KioskHeartbeatView.as_view(),      name='heartbeat'),
]
