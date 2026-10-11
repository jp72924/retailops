from api.pagination import CappedPageNumberPagination
from api.storefront.serializers import LIVE_CURRENCY_SCHEMA, live_currency_payload


class StorefrontPagination(CappedPageNumberPagination):
    """The standard page envelope, plus the live `currency` block."""

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data['currency'] = live_currency_payload()
        return response

    def get_paginated_response_schema(self, schema):
        page = super().get_paginated_response_schema(schema)
        page['properties']['currency'] = LIVE_CURRENCY_SCHEMA
        page['required'] = [*page.get('required', []), 'currency']
        return page
