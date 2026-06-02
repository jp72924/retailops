from django.test import SimpleTestCase

from retailops import settings as retailops_settings
from retailops.storage import RoutedS3Storage


class MediaStorageConfigTests(SimpleTestCase):
    def test_default_media_storage_is_local_filesystem(self):
        config = retailops_settings._media_storage_config_from_env({})

        self.assertEqual(
            config['default']['BACKEND'],
            'django.core.files.storage.FileSystemStorage',
        )
        self.assertNotIn('OPTIONS', config['default'])
        self.assertEqual(
            config['staticfiles']['BACKEND'],
            'django.contrib.staticfiles.storage.StaticFilesStorage',
        )

    def test_gcs_media_storage_routes_public_and_private_buckets(self):
        config = retailops_settings._media_storage_config_from_env({
            'MEDIA_STORAGE_BACKEND': 'gcs',
            'MEDIA_GCS_PROJECT_ID': 'media-project',
            'MEDIA_GCS_PUBLIC_BUCKET_NAME': 'retailops-public-assets',
            'MEDIA_GCS_PRIVATE_BUCKET_NAME': 'retailops-private-documents',
            'MEDIA_GCS_PUBLIC_CUSTOM_ENDPOINT': 'https://assets.example.com',
            'MEDIA_GCS_IAM_SIGN_BLOB': 'true',
            'MEDIA_GCS_SERVICE_ACCOUNT_EMAIL': 'media-signer@example.iam.gserviceaccount.com',
            'MEDIA_GCS_SIGNED_URL_EXPIRATION': '600',
        })

        self.assertEqual(config['default']['BACKEND'], 'retailops.storage.RoutedGoogleCloudStorage')
        options = config['default']['OPTIONS']
        self.assertEqual(options['project_id'], 'media-project')
        self.assertEqual(options['product_bucket_name'], 'retailops-public-assets')
        self.assertEqual(options['receipt_bucket_name'], 'retailops-private-documents')
        self.assertEqual(options['default_bucket_name'], 'retailops-public-assets')
        self.assertEqual(options['product_custom_endpoint'], 'https://assets.example.com')
        self.assertTrue(options['iam_sign_blob'])
        self.assertEqual(options['sa_email'], 'media-signer@example.iam.gserviceaccount.com')
        self.assertEqual(options['signed_url_expiration'], 600)
        self.assertFalse(options['product_querystring_auth'])
        self.assertTrue(options['receipt_querystring_auth'])

    def test_gcs_media_storage_accepts_one_generic_bucket(self):
        config = retailops_settings._media_storage_config_from_env({
            'MEDIA_STORAGE_BACKEND': 'google-cloud-storage',
            'MEDIA_GCS_BUCKET_NAME': 'retailops-media',
        })

        options = config['default']['OPTIONS']
        self.assertEqual(options['default_bucket_name'], 'retailops-media')
        self.assertEqual(options['product_bucket_name'], 'retailops-media')
        self.assertEqual(options['receipt_bucket_name'], 'retailops-media')

    def test_s3_media_storage_routes_public_and_private_buckets(self):
        config = retailops_settings._media_storage_config_from_env({
            'MEDIA_STORAGE_BACKEND': 's3',
            'MEDIA_S3_ENDPOINT_URL': 'http://127.0.0.1:9000',
            'MEDIA_S3_ACCESS_KEY_ID': 'retailops',
            'MEDIA_S3_SECRET_ACCESS_KEY': 'secret',
            'MEDIA_S3_PUBLIC_BUCKET_NAME': 'retailops-public-assets',
            'MEDIA_S3_PRIVATE_BUCKET_NAME': 'retailops-private-documents',
            'MEDIA_S3_REGION_NAME': 'local',
            'MEDIA_S3_SIGNED_URL_EXPIRATION': '600',
        })

        self.assertEqual(config['default']['BACKEND'], 'retailops.storage.RoutedS3Storage')
        options = config['default']['OPTIONS']
        self.assertEqual(options['endpoint_url'], 'http://127.0.0.1:9000')
        self.assertEqual(options['access_key'], 'retailops')
        self.assertEqual(options['secret_key'], 'secret')
        self.assertEqual(options['region_name'], 'local')
        self.assertEqual(options['product_bucket_name'], 'retailops-public-assets')
        self.assertEqual(options['receipt_bucket_name'], 'retailops-private-documents')
        self.assertEqual(options['default_bucket_name'], 'retailops-public-assets')
        self.assertEqual(options['signed_url_expiration'], 600)
        self.assertEqual(options['addressing_style'], 'path')
        self.assertEqual(options['signature_version'], 's3v4')
        self.assertFalse(options['product_querystring_auth'])
        self.assertTrue(options['receipt_querystring_auth'])

    def test_s3_media_storage_accepts_one_generic_bucket(self):
        config = retailops_settings._media_storage_config_from_env({
            'MEDIA_STORAGE_BACKEND': 's3-compatible',
            'MEDIA_S3_ENDPOINT_URL': 'http://127.0.0.1:9000',
            'MEDIA_S3_ACCESS_KEY_ID': 'retailops',
            'MEDIA_S3_SECRET_ACCESS_KEY': 'secret',
            'MEDIA_S3_BUCKET_NAME': 'retailops-media',
        })

        options = config['default']['OPTIONS']
        self.assertEqual(options['default_bucket_name'], 'retailops-media')
        self.assertEqual(options['product_bucket_name'], 'retailops-media')
        self.assertEqual(options['receipt_bucket_name'], 'retailops-media')

    def test_s3_media_storage_requires_endpoint_buckets_and_credentials(self):
        with self.assertRaisesRegex(RuntimeError, 'MEDIA_S3_ENDPOINT_URL'):
            retailops_settings._media_storage_config_from_env({
                'MEDIA_STORAGE_BACKEND': 's3',
            })

    def test_s3_media_storage_rejects_invalid_boolean(self):
        with self.assertRaisesRegex(RuntimeError, 'MEDIA_S3_PRODUCT_PUBLIC'):
            retailops_settings._media_storage_config_from_env({
                'MEDIA_STORAGE_BACKEND': 's3',
                'MEDIA_S3_ENDPOINT_URL': 'http://127.0.0.1:9000',
                'MEDIA_S3_ACCESS_KEY_ID': 'retailops',
                'MEDIA_S3_SECRET_ACCESS_KEY': 'secret',
                'MEDIA_S3_BUCKET_NAME': 'retailops-media',
                'MEDIA_S3_PRODUCT_PUBLIC': 'maybe',
            })

    def test_routed_s3_storage_selects_bucket_by_prefix(self):
        storage = RoutedS3Storage(
            endpoint_url='http://127.0.0.1:9000',
            access_key='retailops',
            secret_key='secret',
            region_name='local',
            default_bucket_name='retailops-default',
            product_bucket_name='retailops-products',
            receipt_bucket_name='retailops-receipts',
            product_querystring_auth=False,
            receipt_querystring_auth=True,
        )

        self.assertEqual(
            storage._storage_for_name('products/2026/05/item.png').bucket_name,
            'retailops-products',
        )
        self.assertEqual(
            storage._storage_for_name('receipts/2026/05/receipt.png').bucket_name,
            'retailops-receipts',
        )
        self.assertEqual(
            storage._storage_for_name('misc/file.txt').bucket_name,
            'retailops-default',
        )
        self.assertFalse(storage.product_storage.querystring_auth)
        self.assertTrue(storage.receipt_storage.querystring_auth)

    def test_gcs_media_storage_requires_buckets(self):
        with self.assertRaisesRegex(RuntimeError, 'MEDIA_GCS_BUCKET_NAME'):
            retailops_settings._media_storage_config_from_env({
                'MEDIA_STORAGE_BACKEND': 'gcs',
            })

    def test_unsupported_media_storage_backend_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'Unsupported MEDIA_STORAGE_BACKEND'):
            retailops_settings._media_storage_config_from_env({
                'MEDIA_STORAGE_BACKEND': 'ftp',
            })

    def test_invalid_media_boolean_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'MEDIA_GCS_PRODUCT_PUBLIC'):
            retailops_settings._media_storage_config_from_env({
                'MEDIA_STORAGE_BACKEND': 'gcs',
                'MEDIA_GCS_BUCKET_NAME': 'retailops-media',
                'MEDIA_GCS_PRODUCT_PUBLIC': 'maybe',
            })
