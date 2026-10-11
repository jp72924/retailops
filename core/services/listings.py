"""
What a storefront is allowed to see of the catalogue.

A listing is visible when it is published and at least one of its variants is
an active product. A variant whose product is inactive is hidden, never shown
as sold out: inactive means withdrawn from sale. Visibility is decided here, in
one place, so the list, the detail and the category tree cannot disagree.

Availability is a coarse label, deliberately not a number. A storefront has no
use for exact stock, and publishing it would hand a competitor the inventory.
"""
from collections import defaultdict

from django.db import transaction
from django.db.models import Exists, IntegerField, OuterRef, Prefetch, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils.text import slugify

from core.models import Listing, ListingVariant, ProductCategory

UNLIMITED = 'unlimited'
IN_STOCK = 'in_stock'
LOW_STOCK = 'low_stock'
OUT_OF_STOCK = 'out_of_stock'

# Best first: a listing is as available as its most available variant.
_AVAILABILITY_RANK = {UNLIMITED: 0, IN_STOCK: 1, LOW_STOCK: 2, OUT_OF_STOCK: 3}

VARIANTS_ATTR = 'storefront_variants'


def variant_availability(product, stock):
    """`stock` is the product's summed movements; ignored when it is untracked."""
    if not product.track_inventory:
        return UNLIMITED
    if stock <= 0:
        return OUT_OF_STOCK
    if stock <= product.low_stock_threshold:
        return LOW_STOCK
    return IN_STOCK


def listing_availability(availabilities):
    return min(availabilities, key=_AVAILABILITY_RANK.__getitem__, default=OUT_OF_STOCK)


def visible_variants():
    """Variants a storefront may show, each carrying its product's stock as `_stock`."""
    return (
        ListingVariant.objects
        .filter(product__is_active=True)
        .select_related('product')
        .annotate(
            _stock=Coalesce(
                Sum('product__inventory_movements__quantity'),
                Value(0, output_field=IntegerField()),
            )
        )
        .order_by('position', 'pk')
    )


def published_listings():
    """
    Published listings with their visible variants prefetched into
    `storefront_variants`, and the lowest variant price as `from_price`.

    from_price is a correlated subquery rather than Min() over the join: an
    aggregate would force a GROUP BY, and pagination would then have to
    materialise every group just to count them.
    """
    live_variants = ListingVariant.objects.filter(
        listing=OuterRef('pk'), product__is_active=True,
    )
    cheapest = live_variants.order_by('product__unit_price').values('product__unit_price')[:1]
    return (
        Listing.objects
        .filter(is_published=True)
        .filter(Exists(live_variants))
        .select_related('category')
        .annotate(from_price=Subquery(cheapest))
        .prefetch_related(
            Prefetch('variants', queryset=visible_variants(), to_attr=VARIANTS_ATTR)
        )
    )


def orderable_products(skus):
    """
    ``{sku: Product}`` for the SKUs a channel may order.

    The same rule as the catalogue, so a channel can order exactly what it can
    see: a variant of a published listing whose product is active. A SKU that
    belongs to no listing, or to an unpublished one, is simply absent; an
    internal product cannot be bought by guessing its SKU.
    """
    variants = (
        ListingVariant.objects
        .filter(listing__is_published=True, product__is_active=True, product__sku__in=list(skus))
        .select_related('product')
    )
    return {variant.product.sku: variant.product for variant in variants}


def category_and_descendants(category_id):
    """Ids of a category and of every category beneath it."""
    children = defaultdict(list)
    for pk, parent_id in ProductCategory.objects.values_list('pk', 'parent_category_id'):
        children[parent_id].append(pk)
    found, pending = {category_id}, [category_id]
    while pending:
        for child in children[pending.pop()]:
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def category_tree():
    """
    Categories that lead to at least one published, visible listing, as a
    nested list. Each node's `listing_count` includes its descendants; empty
    branches are pruned.
    """
    live_variants = ListingVariant.objects.filter(
        listing=OuterRef('pk'), product__is_active=True,
    )
    own = defaultdict(int)
    for category_id in (
        Listing.objects.filter(is_published=True).filter(Exists(live_variants))
        .values_list('category_id', flat=True)
    ):
        own[category_id] += 1

    rows = list(ProductCategory.objects.order_by('name').values(
        'pk', 'name', 'description', 'parent_category_id',
    ))
    by_parent = defaultdict(list)
    for row in rows:
        by_parent[row['parent_category_id']].append(row)

    def build(row, trail):
        # A cycle can only come from hand-edited data; stop rather than recurse.
        if row['pk'] in trail:
            return None
        children = [
            node for child in by_parent[row['pk']]
            if (node := build(child, trail | {row['pk']})) is not None
        ]
        count = own[row['pk']] + sum(child['listing_count'] for child in children)
        if not count:
            return None
        return {
            'id': row['pk'],
            'name': row['name'],
            'description': row['description'],
            'listing_count': count,
            'children': children,
        }

    return [node for row in by_parent[None] if (node := build(row, frozenset())) is not None]


def _unique_slug(base):
    slug = base = (slugify(base) or 'product')[:110]
    suffix = 2
    while Listing.objects.filter(slug=slug).exists():
        slug = f'{base}-{suffix}'
        suffix += 1
    return slug


@transaction.atomic
def publish_product(product):
    """
    List a product on the storefront catalogue.

    Returns ``(listing, created)``. A product already in a listing is left
    where it is; publishing never moves a product between listings, and never
    republishes one an operator unpublished on purpose.
    """
    existing = ListingVariant.objects.select_related('listing').filter(product=product).first()
    if existing is not None:
        return existing.listing, False

    listing = Listing.objects.create(
        slug=_unique_slug(product.sku),
        title=product.name,
        category=product.category,
        display_type=product.kind,
        is_published=True,
    )
    ListingVariant.objects.create(listing=listing, product=product)
    return listing, True
