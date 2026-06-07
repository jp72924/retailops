from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

app_name = 'api'

from .views.auth import (
    MeView,
    ObtainTokenView,
    PasswordResetConfirmView,
    PasswordResetRequestView,
    RevokeTokenView,
)
from .views.category import ProductCategoryViewSet
from .views.customer import CustomerViewSet
from .views.dashboard import DashboardView
from .views.inventory import InventoryMovementViewSet
from .views.order import OrderViewSet
from .views.payment import PaymentViewSet
from .views.product import ProductViewSet
from .views.role import RoleViewSet
from .views.mcp_skill import MCPSkillView
from .views.settings import SecondaryRateRefreshView, SystemSettingsView
from .views.user import UserViewSet

router = DefaultRouter()

# ── Phase 2 ───────────────────────────────────────────────────────────────────
router.register('roles',      RoleViewSet,            basename='role')
router.register('users',      UserViewSet,            basename='user')
router.register('customers',  CustomerViewSet,        basename='customer')
router.register('categories', ProductCategoryViewSet, basename='category')

# ── Phase 3 ───────────────────────────────────────────────────────────────────
router.register('products',  ProductViewSet,          basename='product')
router.register('inventory', InventoryMovementViewSet, basename='inventory')

# ── Phase 4 ───────────────────────────────────────────────────────────────────
router.register('orders',   OrderViewSet,   basename='order')
router.register('payments', PaymentViewSet, basename='payment')

urlpatterns = router.urls + [
    # Authentication
    path('auth/token/',                   ObtainTokenView.as_view(),         name='api-token-obtain'),
    path('auth/token/revoke/',            RevokeTokenView.as_view(),         name='api-token-revoke'),
    path('auth/me/',                      MeView.as_view(),                  name='api-me'),
    path('auth/password-reset/',          PasswordResetRequestView.as_view(), name='api-password-reset'),
    path('auth/password-reset/confirm/',  PasswordResetConfirmView.as_view(), name='api-password-reset-confirm'),

    # Phase 5 — dashboard
    path('dashboard/', DashboardView.as_view(), name='api-dashboard'),

    # System settings (currency, etc.)
    path('settings/', SystemSettingsView.as_view(), name='api-settings'),
    path('settings/secondary-rate/refresh/', SecondaryRateRefreshView.as_view(), name='api-secondary-rate-refresh'),

    # MCP skill card — public capability descriptor for AI agents
    path('mcp-skill/', MCPSkillView.as_view(), name='api-mcp-skill'),

    # Kiosk integration — per-station API key auth, isolated namespace
    path('kiosk/', include('api.kiosk.urls')),

    # Phase 5 — OpenAPI schema (drf-spectacular)
    path('schema/',          SpectacularAPIView.as_view(),                        name='api-schema'),
    path('schema/swagger/',  SpectacularSwaggerView.as_view(url_name='api:api-schema'), name='api-swagger'),
    path('schema/redoc/',    SpectacularRedocView.as_view(url_name='api:api-schema'),   name='api-redoc'),
]
