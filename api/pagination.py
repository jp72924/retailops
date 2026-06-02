from rest_framework.pagination import PageNumberPagination


class CappedPageNumberPagination(PageNumberPagination):
    """
    Standard page-number pagination with a hard cap on client-requested page sizes.

    Clients may request a custom page size via ?page_size=N.
    The cap (max_page_size) prevents a single request from fetching the entire
    table — keep it at 100 unless there is a specific reason to raise it.

    Default page size is inherited from REST_FRAMEWORK['PAGE_SIZE'] in settings.
    """
    page_size_query_param = 'page_size'
    max_page_size = 100
