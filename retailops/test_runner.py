"""
retailops.test_runner
---------------------
Test runner that keeps file uploads out of the real media directory.
"""

import shutil
import tempfile

from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class MediaIsolatedTestRunner(DiscoverRunner):
    """
    Point MEDIA_ROOT at a throwaway directory for the duration of the run.

    Django isolates the database for tests but not the filesystem, so any test
    that saves a FileField writes into the real MEDIA_ROOT and leaves it there.
    Several hundred orphaned fixture blobs had built up under media/receipts/
    that way — harmless individually, but they share a directory with real
    uploads, which makes "is this file mine?" unanswerable.

    Note that overriding MEDIA_ROOT is not sufficient on its own.
    django.test.signals.storages_changed resets the cached default_storage for
    STORAGES / STATIC_ROOT / STATIC_URL but *not* for MEDIA_ROOT, and
    FileSystemStorage.base_location is a cached_property. Whichever value was
    live the first time default_storage was touched would otherwise stick for
    the whole run. override_settings' setting_changed signal is what clears it
    here, via the explicit reset below.
    """

    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._temp_media_root = tempfile.mkdtemp(prefix='retailops-test-media-')
        self._media_override = override_settings(MEDIA_ROOT=self._temp_media_root)
        self._media_override.enable()
        self._reset_default_storage()

    def teardown_test_environment(self, **kwargs):
        self._media_override.disable()
        self._reset_default_storage()
        shutil.rmtree(self._temp_media_root, ignore_errors=True)
        super().teardown_test_environment(**kwargs)

    @staticmethod
    def _reset_default_storage():
        """Drop the cached storage so it re-reads MEDIA_ROOT on next access."""
        from django.core.files.storage import default_storage, storages
        from django.utils.functional import empty

        default_storage._wrapped = empty
        try:
            del storages.backends
        except AttributeError:
            pass
        storages._backends = None
        storages._storages = {}
