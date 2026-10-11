"""System checks for configuration that cannot be validated at import."""
from django.conf import settings
from django.core.checks import Warning, register


@register()
def entitlement_keys_configured(app_configs, **kwargs):
    if settings.ENTITLEMENT_ENCRYPTION_KEYS:
        return []
    return [Warning(
        'ENTITLEMENT_ENCRYPTION_KEYS is not set, so a licence key cannot be stored for delivery.',
        hint=(
            'Set it to one or more comma-separated Fernet keys; see the comment above '
            'ENTITLEMENT_ENCRYPTION_KEYS in retailops/settings.py.'
        ),
        id='core.W001',
    )]
