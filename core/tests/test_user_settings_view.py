from django.test import TestCase
from django.urls import reverse

from core.models import Role, SystemSettings, User


class UserSettingsViewTests(TestCase):
    def setUp(self):
        role = Role.objects.create(name=Role.ADMIN)
        self.user = User.objects.create_user(
            email='admin@example.com',
            password='AdminPass123!',
            first_name='Ada',
            last_name='Admin',
            role=role,
        )
        self.client.force_login(self.user)
        self.url = reverse('settings')

    def test_save_settings_with_no_ocr_methods_checked_persists(self):
        """
        Regression test: SystemSettings.ocr_enabled_methods is a JSONField
        with default=list and no blank=True. Django's full_clean() treats
        [] as an empty value, so omitting all OCR method checkboxes (the
        default/common state) used to raise a spurious "cannot be blank"
        ValidationError and silently drop the whole settings save.
        """
        response = self.client.post(self.url, {
            'timezone': 'UTC',
            'language': 'en',
            'currency_code': 'USD',
            'currency_symbol': '$',
            'decimal_places': '3',
        })

        # On success the view redirects to itself; a re-render with the
        # "cannot be blank" error (the bug) is a 200 with a populated
        # errors context instead.
        self.assertEqual(response.status_code, 302)

        sys_settings = SystemSettings.get()
        self.assertEqual(sys_settings.decimal_places, 3)
        self.assertEqual(sys_settings.ocr_enabled_methods, [])
