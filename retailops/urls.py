from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
from django.urls import path, include

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/v1/', include('api.urls', namespace='api')),
    path('', include('core.urls')),
]

if settings.DEBUG and getattr(settings, 'MEDIA_STORAGE_BACKEND', 'local') == 'local':
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
