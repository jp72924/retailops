"""
Domain exceptions for RetailOps business rules.

These are raised by the model and service layer and translated into
HTTP responses by whichever surface is calling -- the REST API, the
back-office views, or the kiosk.  Keeping them here (rather than in
models.py or a view module) lets any layer catch them without importing
the ORM or DRF, and keeps the rule and its failure mode defined together.
"""


class DomainError(Exception):
    """Base class for a violated business rule."""


class OrderStateError(DomainError):
    """An order transition was attempted from the wrong status."""

    def __init__(self, expected, actual):
        self.expected = expected
        self.actual = actual
        super().__init__(
            f'This action requires status "{expected}"; '
            f'order is currently "{actual}".'
        )


class EmptyOrderError(DomainError):
    """An order with no line items cannot be confirmed."""

    def __init__(self, message='Cannot confirm an order with no line items.'):
        super().__init__(message)


class InsufficientStockError(DomainError):
    """
    A stock deduction would drive one or more products negative.

    ``shortfalls`` is a list of dicts -- one per offending product -- shaped
        {'sku': str, 'product_id': int, 'requested': int, 'available': int}

    Every offending product is reported, not just the first, so an operator
    can fix the whole order in one pass instead of discovering shortfalls one
    confirmation at a time.
    """

    def __init__(self, shortfalls):
        self.shortfalls = list(shortfalls)
        skus = ', '.join(str(s['sku']) for s in self.shortfalls)
        super().__init__(f'Insufficient stock for: {skus}')
