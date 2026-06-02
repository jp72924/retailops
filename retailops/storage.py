from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import Storage


class RoutedGoogleCloudStorage(Storage):
    """
    Route media objects to independent Google Cloud Storage buckets by prefix.

    Product images are public assets by default. Receipt images are private
    evidence and keep signed URLs enabled by default.
    """

    product_prefixes = ('products/',)
    receipt_prefixes = ('receipts/',)

    def __init__(
        self,
        *,
        default_bucket_name,
        product_bucket_name,
        receipt_bucket_name,
        project_id=None,
        default_querystring_auth=True,
        product_querystring_auth=False,
        receipt_querystring_auth=True,
        file_overwrite=False,
        signed_url_expiration=900,
        iam_sign_blob=False,
        sa_email=None,
        default_custom_endpoint=None,
        product_custom_endpoint=None,
        receipt_custom_endpoint=None,
        default_acl=None,
        product_default_acl=None,
        receipt_default_acl=None,
        product_cache_control=None,
        receipt_cache_control=None,
    ):
        try:
            from storages.backends.gcloud import GoogleCloudStorage
        except ImportError as exc:
            raise ImproperlyConfigured(
                'MEDIA_STORAGE_BACKEND=gcs requires django-storages with the '
                'Google extra. Run `pip install -r requirements.txt` first.'
            ) from exc

        self.default_storage = self._build_storage(
            GoogleCloudStorage,
            bucket_name=default_bucket_name,
            project_id=project_id,
            querystring_auth=default_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            iam_sign_blob=iam_sign_blob,
            sa_email=sa_email,
            custom_endpoint=default_custom_endpoint,
            default_acl=default_acl,
            cache_control=None,
        )
        self.product_storage = self._build_storage(
            GoogleCloudStorage,
            bucket_name=product_bucket_name,
            project_id=project_id,
            querystring_auth=product_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            iam_sign_blob=iam_sign_blob,
            sa_email=sa_email,
            custom_endpoint=product_custom_endpoint or default_custom_endpoint,
            default_acl=product_default_acl,
            cache_control=product_cache_control,
        )
        self.receipt_storage = self._build_storage(
            GoogleCloudStorage,
            bucket_name=receipt_bucket_name,
            project_id=project_id,
            querystring_auth=receipt_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            iam_sign_blob=iam_sign_blob,
            sa_email=sa_email,
            custom_endpoint=receipt_custom_endpoint or default_custom_endpoint,
            default_acl=receipt_default_acl,
            cache_control=receipt_cache_control,
        )

    def _build_storage(
        self,
        storage_class,
        *,
        bucket_name,
        project_id,
        querystring_auth,
        file_overwrite,
        signed_url_expiration,
        iam_sign_blob,
        sa_email,
        custom_endpoint,
        default_acl,
        cache_control,
    ):
        options = {
            'bucket_name': bucket_name,
            'project_id': project_id,
            'querystring_auth': querystring_auth,
            'file_overwrite': file_overwrite,
            'expiration': signed_url_expiration,
            'iam_sign_blob': iam_sign_blob,
            'sa_email': sa_email,
            'custom_endpoint': custom_endpoint,
            'default_acl': default_acl,
        }
        object_parameters = {}
        if cache_control:
            object_parameters['cache_control'] = cache_control
        if object_parameters:
            options['object_parameters'] = object_parameters

        return storage_class(**{
            key: value
            for key, value in options.items()
            if value is not None
        })

    def _storage_for_name(self, name):
        normalized = str(name or '').replace('\\', '/').lstrip('/')
        if normalized.startswith(self.product_prefixes):
            return self.product_storage
        if normalized.startswith(self.receipt_prefixes):
            return self.receipt_storage
        return self.default_storage

    def _open(self, name, mode='rb'):
        return self._storage_for_name(name).open(name, mode)

    def _save(self, name, content):
        return self._storage_for_name(name).save(name, content)

    def delete(self, name):
        return self._storage_for_name(name).delete(name)

    def exists(self, name):
        return self._storage_for_name(name).exists(name)

    def listdir(self, path):
        return self._storage_for_name(path).listdir(path)

    def size(self, name):
        return self._storage_for_name(name).size(name)

    def url(self, name):
        return self._storage_for_name(name).url(name)

    def get_available_name(self, name, max_length=None):
        return self._storage_for_name(name).get_available_name(name, max_length=max_length)

    def get_valid_name(self, name):
        return self.default_storage.get_valid_name(name)

    def generate_filename(self, filename):
        return self._storage_for_name(filename).generate_filename(filename)

    def get_accessed_time(self, name):
        return self._storage_for_name(name).get_accessed_time(name)

    def get_created_time(self, name):
        return self._storage_for_name(name).get_created_time(name)

    def get_modified_time(self, name):
        return self._storage_for_name(name).get_modified_time(name)


class RoutedS3Storage(Storage):
    """
    Route media objects to independent S3-compatible buckets by prefix.

    This supports local/bare-metal providers such as RustFS and Garage while
    preserving the same product/receipt split used by the GCS storage backend.
    """

    product_prefixes = ('products/',)
    receipt_prefixes = ('receipts/',)

    def __init__(
        self,
        *,
        endpoint_url,
        access_key,
        secret_key,
        region_name,
        default_bucket_name,
        product_bucket_name,
        receipt_bucket_name,
        default_querystring_auth=True,
        product_querystring_auth=False,
        receipt_querystring_auth=True,
        file_overwrite=False,
        signed_url_expiration=900,
        addressing_style='path',
        signature_version='s3v4',
        default_acl=None,
        product_default_acl=None,
        receipt_default_acl=None,
        product_cache_control=None,
        receipt_cache_control=None,
    ):
        try:
            from storages.backends.s3 import S3Storage
        except (ImportError, ImproperlyConfigured) as exc:
            raise ImproperlyConfigured(
                'MEDIA_STORAGE_BACKEND=s3 requires django-storages with the '
                'S3 extra. Run `pip install -r requirements.txt` first.'
            ) from exc

        use_ssl = str(endpoint_url).lower().startswith('https://')

        self.default_storage = self._build_storage(
            S3Storage,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region_name=region_name,
            bucket_name=default_bucket_name,
            querystring_auth=default_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            addressing_style=addressing_style,
            signature_version=signature_version,
            default_acl=default_acl,
            cache_control=None,
            use_ssl=use_ssl,
        )
        self.product_storage = self._build_storage(
            S3Storage,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region_name=region_name,
            bucket_name=product_bucket_name,
            querystring_auth=product_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            addressing_style=addressing_style,
            signature_version=signature_version,
            default_acl=product_default_acl,
            cache_control=product_cache_control,
            use_ssl=use_ssl,
        )
        self.receipt_storage = self._build_storage(
            S3Storage,
            endpoint_url=endpoint_url,
            access_key=access_key,
            secret_key=secret_key,
            region_name=region_name,
            bucket_name=receipt_bucket_name,
            querystring_auth=receipt_querystring_auth,
            file_overwrite=file_overwrite,
            signed_url_expiration=signed_url_expiration,
            addressing_style=addressing_style,
            signature_version=signature_version,
            default_acl=receipt_default_acl,
            cache_control=receipt_cache_control,
            use_ssl=use_ssl,
        )

    def _build_storage(
        self,
        storage_class,
        *,
        endpoint_url,
        access_key,
        secret_key,
        region_name,
        bucket_name,
        querystring_auth,
        file_overwrite,
        signed_url_expiration,
        addressing_style,
        signature_version,
        default_acl,
        cache_control,
        use_ssl,
    ):
        options = {
            'endpoint_url': endpoint_url,
            'access_key': access_key,
            'secret_key': secret_key,
            'region_name': region_name,
            'bucket_name': bucket_name,
            'querystring_auth': querystring_auth,
            'querystring_expire': signed_url_expiration,
            'file_overwrite': file_overwrite,
            'addressing_style': addressing_style,
            'signature_version': signature_version,
            'default_acl': default_acl,
            'use_ssl': use_ssl,
        }
        object_parameters = {}
        if cache_control:
            object_parameters['CacheControl'] = cache_control
        if object_parameters:
            options['object_parameters'] = object_parameters

        return storage_class(**{
            key: value
            for key, value in options.items()
            if value is not None
        })

    def _storage_for_name(self, name):
        normalized = str(name or '').replace('\\', '/').lstrip('/')
        if normalized.startswith(self.product_prefixes):
            return self.product_storage
        if normalized.startswith(self.receipt_prefixes):
            return self.receipt_storage
        return self.default_storage

    def _open(self, name, mode='rb'):
        return self._storage_for_name(name).open(name, mode)

    def _save(self, name, content):
        return self._storage_for_name(name).save(name, content)

    def delete(self, name):
        return self._storage_for_name(name).delete(name)

    def exists(self, name):
        return self._storage_for_name(name).exists(name)

    def listdir(self, path):
        return self._storage_for_name(path).listdir(path)

    def size(self, name):
        return self._storage_for_name(name).size(name)

    def url(self, name):
        return self._storage_for_name(name).url(name)

    def get_available_name(self, name, max_length=None):
        return self._storage_for_name(name).get_available_name(name, max_length=max_length)

    def get_valid_name(self, name):
        return self.default_storage.get_valid_name(name)

    def generate_filename(self, filename):
        return self._storage_for_name(filename).generate_filename(filename)

    def get_accessed_time(self, name):
        return self._storage_for_name(name).get_accessed_time(name)

    def get_created_time(self, name):
        return self._storage_for_name(name).get_created_time(name)

    def get_modified_time(self, name):
        return self._storage_for_name(name).get_modified_time(name)
