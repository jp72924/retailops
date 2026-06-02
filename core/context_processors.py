from core.models import SystemSettings


def system_settings(request):
    """Expose the SystemSettings singleton to every template as `system_settings`."""
    try:
        return {'system_settings': SystemSettings.get()}
    except Exception:
        return {'system_settings': None}
