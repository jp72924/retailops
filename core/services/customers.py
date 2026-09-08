"""
Customer identity rules.

One place decides what a national ID means and when two of them are the same
person. Every write path -- the REST API, both back-office forms, the order
form's quick-create modal, and kiosk registration -- goes through
`check_national_id` so the rule cannot differ between surfaces.

Why this exists: matching ignores punctuation and case, so "V-12.345.678" and
"V12345678" are one person. Uniqueness used to be enforced on the raw string,
which let both spellings coexist and produced a customer the order form could
not resolve and the kiosk could not find.
"""
from django.db.models import F, Value
from django.db.models.functions import Replace, Upper

from core.services.receipt_matching import normalize_document_id


def normalized_national_id_expr():
    """
    SQL expression that strips ID punctuation and upper-cases, so a stored
    "V-12.345.678" can be compared against a normalized "V12345678".

    Approximates normalize_document_id(), which strips every non-alphanumeric
    via regex -- matching it exactly would mean pulling every row into Python.
    REPLACE/UPPER are ANSI SQL and behave the same on SQLite and PostgreSQL.

    New rows are normalized by Customer.save(), so this only has to reach rows
    written before that: it is a compatibility path, not the primary rule.
    """
    expr = F('national_id')
    for ch in ('-', '.', ' ', '/', ',', '_'):
        expr = Replace(expr, Value(ch), Value(''))
    return Upper(expr)


def find_customer_by_national_id(raw_national_id, exclude_pk=None):
    """
    Resolve a customer by national ID, tolerating punctuation on either side.

    Two steps, in this order:

    1. Exact match on the normalized value. Every row written since IDs became
       normalized on save stores exactly this, so it is the indexed fast path.
    2. SQL-normalized comparison, which reaches rows written before that -- a
       stored "V-12.345.678" is otherwise invisible to a shopper typing
       "V12345678", which is the bug this whole path exists to prevent.

    Returns the lowest-pk match so a legacy pair that normalizes alike resolves
    deterministically instead of raising MultipleObjectsReturned.
    """
    from core.models import Customer

    normalized = normalize_document_id(raw_national_id)
    if not normalized:
        return None

    base = Customer.objects.all()
    if exclude_pk is not None:
        base = base.exclude(pk=exclude_pk)

    exact = base.filter(national_id__in=[raw_national_id, normalized]).order_by('pk').first()
    if exact is not None:
        return exact

    return (
        base
        .exclude(national_id__isnull=True).exclude(national_id='')
        .annotate(_norm_nid=normalized_national_id_expr())
        .filter(_norm_nid=normalized)
        .order_by('pk')
        .first()
    )


def find_conflicting_customer(raw_national_id, exclude_pk=None):
    """The customer already holding this ID under normalization, or None."""
    return find_customer_by_national_id(raw_national_id, exclude_pk=exclude_pk)


def check_national_id(raw_national_id, *, exclude_pk=None, required=True):
    """
    Validate a national ID for any write path.

    Returns the normalized value. Raises ValueError with a user-facing message
    if it is missing (when required) or already registered to someone else.

    Callers translate the ValueError into their own surface's error format --
    a DRF ValidationError, a form `errors` dict, or a JSON error payload.
    """
    normalized = normalize_document_id(raw_national_id)

    if not normalized:
        if required:
            raise ValueError('An ID number is required.')
        return None

    if find_conflicting_customer(raw_national_id, exclude_pk=exclude_pk) is not None:
        raise ValueError('This ID number is already registered.')

    return normalized
