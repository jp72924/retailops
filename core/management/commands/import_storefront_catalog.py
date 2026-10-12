"""Load a storefront's catalogue from a JSON file and provision its sales channel.

    python manage.py import_storefront_catalog --catalog editorial.json \\
        --dev-vars ../trend-store/apps/editorial/.dev.vars --base-url http://127.0.0.1:8000

It is idempotent: products are matched by SKU and listings by slug, so running it again updates
what changed and creates nothing twice. Every listing it imports is published to the file's
channel only, so stores with different assortments can share one RetailOps. The channel is
created if it does not exist, with a first key printed once (and written to `--dev-vars`, which is
never overwritten once it holds a key unless `--new-key` is given).

The file looks like this; only `channel` and `listings` are required:

    {
      "version": 1,
      "channel": {"slug": "editorial", "name": "Trend Store Editorial",
                  "trusts_email_verification": true, "order_expiry_minutes": 15},
      "listings": [{
        "slug": "nova-productivity-pro", "title": "Nova Productivity Pro", "summary": "...",
        "description": "...", "badge": "BEST SELLER", "display_type": "license",
        "category": "Productivity", "sort": 1, "features": ["..."], "attributes": {"platform": "..."},
        "variants": [{
          "sku": "ED-NOVA-LIFE", "label": "Lifetime license", "price": "79.00",
          "compare_at_price": null, "kind": "digital", "track_inventory": false, "stock": 0,
          "image_url": "https://...", "fulfillment": {"mode": "supplied", "kind": "key",
          "duration_days": null, "per_unit": false, "template": ""}
        }]
      }]
    }
"""
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from api.storefront.provisioning import create_channel, issue_key
from core.management.scratch import SCRATCH_DB_ADVICE, refuses_default_database
from core.models import (
    InventoryMovement,
    Listing,
    ListingVariant,
    Product,
    ProductCategory,
    ProductFulfillment,
    SalesChannel,
    User,
)

DEFAULT_IMAGE = 'https://images.example.com/products/{sku}.png'
KINDS = {kind for kind, _label in Product.KIND_CHOICES}


class ImportProblems(Exception):
    def __init__(self, problems):
        self.problems = problems
        super().__init__('; '.join(problems))


def _decimal(value, where, problems):
    try:
        return Decimal(str(value)).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        problems.append(f'{where}: {value!r} is not a price')
        return None


def parse_catalog(document):
    """Validate the document; returns it normalised, or raises ImportProblems listing every problem."""
    problems = []
    if not isinstance(document, dict) or document.get('version') != 1:
        raise ImportProblems(['The catalogue must be an object with "version": 1.'])

    channel = document.get('channel') or {}
    if not channel.get('slug') or not channel.get('name'):
        problems.append('channel: "slug" and "name" are required')

    listings = document.get('listings')
    if not isinstance(listings, list) or not listings:
        problems.append('listings: a non-empty list is required')
        raise ImportProblems(problems)

    seen_slugs, seen_skus = set(), set()
    for index, listing in enumerate(listings):
        where = f'listings[{index}]'
        slug = listing.get('slug')
        for field in ('slug', 'title', 'category'):
            if not listing.get(field):
                problems.append(f'{where}: "{field}" is required')
        if slug in seen_slugs:
            problems.append(f'{where}: slug {slug!r} appears twice')
        seen_slugs.add(slug)

        variants = listing.get('variants')
        if not isinstance(variants, list) or not variants:
            problems.append(f'{where}: at least one variant is required')
            continue
        for v_index, variant in enumerate(variants):
            v_where = f'{where}.variants[{v_index}]'
            sku = variant.get('sku')
            if not sku:
                problems.append(f'{v_where}: "sku" is required')
            elif sku in seen_skus:
                problems.append(f'{v_where}: sku {sku!r} appears twice')
            seen_skus.add(sku)
            if 'price' not in variant:
                problems.append(f'{v_where}: "price" is required')
            else:
                _decimal(variant['price'], f'{v_where}.price', problems)
            if variant.get('compare_at_price') is not None:
                _decimal(variant['compare_at_price'], f'{v_where}.compare_at_price', problems)
            if variant.get('kind', Product.PHYSICAL) not in KINDS:
                problems.append(f'{v_where}: kind must be one of {sorted(KINDS)}')
            fulfillment = variant.get('fulfillment')
            if fulfillment is not None and not isinstance(fulfillment, dict):
                problems.append(f'{v_where}: "fulfillment" must be an object')

    if problems:
        raise ImportProblems(problems)
    return document


@transaction.atomic
def import_catalog(document, *, actor, new_key=False):
    """
    Apply a parsed catalogue. Returns ``(summary, raw_key, channel)``; ``raw_key`` is the channel key
    issued by this run (the channel's first, or a new one when asked), or None.
    """
    spec = document['channel']
    summary = {
        'channel': 'existing', 'listings_created': 0, 'listings_updated': 0,
        'products_created': 0, 'products_updated': 0, 'fulfillments': 0, 'stock_adjusted': 0,
    }

    raw_key = None
    channel = SalesChannel.objects.filter(slug=spec['slug']).first()
    if channel is None:
        channel, raw_key = create_channel(
            name=spec['name'], slug=spec['slug'], created_by=actor, label='demo import',
        )
        summary['channel'] = 'created'
    elif new_key:
        _key, raw_key = issue_key(channel, label='demo import')
        summary['channel'] = 'new key issued'

    changed = []
    for field in ('trusts_email_verification', 'order_expiry_minutes'):
        if field in spec and getattr(channel, field) != spec[field]:
            setattr(channel, field, spec[field])
            changed.append(field)
    if changed:
        channel.full_clean(exclude=['service_user', 'created_by'])
        channel.save(update_fields=changed)

    for sort, entry in enumerate(document['listings'], start=1):
        category, _created = ProductCategory.objects.get_or_create(name=entry['category'])

        listing, created = Listing.objects.get_or_create(
            slug=entry['slug'], defaults={'title': entry['title'], 'category': category},
        )
        listing.title = entry['title']
        listing.summary = entry.get('summary', '')
        listing.description = entry.get('description', '')
        listing.badge = entry.get('badge', '')
        listing.display_type = entry.get('display_type', '')
        listing.features = entry.get('features', [])
        listing.attributes = entry.get('attributes', {})
        listing.category = category
        listing.sort = entry.get('sort', sort)
        listing.is_published = True
        listing.full_clean(exclude=['image'])
        listing.save()
        listing.channels.set([channel])
        summary['listings_created' if created else 'listings_updated'] += 1

        for position, variant in enumerate(entry['variants']):
            product = _upsert_product(variant, entry, category, actor, summary)
            existing = ListingVariant.objects.filter(product=product).select_related('listing').first()
            if existing is not None and existing.listing_id != listing.pk:
                raise CommandError(
                    f'SKU {product.sku} already belongs to listing {existing.listing.slug!r}; '
                    f'it cannot also be a variant of {listing.slug!r}.'
                )
            link = existing or ListingVariant(listing=listing, product=product)
            link.label = variant.get('label', '')
            link.position = position
            compare = variant.get('compare_at_price')
            link.compare_at_price = Decimal(str(compare)).quantize(Decimal('0.01')) if compare is not None else None
            link.full_clean()
            link.save()

            if variant.get('fulfillment'):
                _upsert_fulfillment(product, variant['fulfillment'])
                summary['fulfillments'] += 1

    return summary, raw_key, channel


def _upsert_product(variant, entry, category, actor, summary):
    sku = variant['sku']
    product = Product.objects.filter(sku=sku).first()
    created = product is None
    if created:
        product = Product(sku=sku)
    product.name = variant.get('name') or (
        f"{entry['title']} - {variant['label']}" if variant.get('label') else entry['title']
    )
    product.category = category
    product.unit_price = Decimal(str(variant['price'])).quantize(Decimal('0.01'))
    product.kind = variant.get('kind', Product.PHYSICAL)
    product.track_inventory = variant.get('track_inventory', product.kind == Product.PHYSICAL)
    product.low_stock_threshold = variant.get('low_stock_threshold', 2)
    product.description = variant.get('description', entry.get('summary', ''))
    product.is_active = True
    product.external_image_url = variant.get('image_url') or entry.get('image_url') or DEFAULT_IMAGE.format(sku=sku)
    product.full_clean(exclude=['image'])
    product.save()
    summary['products_created' if created else 'products_updated'] += 1

    if product.track_inventory and 'stock' in variant:
        gap = int(variant['stock']) - (product.current_stock or 0)
        if gap:
            InventoryMovement.objects.create(
                product=product,
                movement_type=InventoryMovement.PURCHASE if gap > 0 else InventoryMovement.ADJUSTMENT,
                quantity=gap,
                reference_type=InventoryMovement.MANUAL_ADJUSTMENT,
                reference_id=0,
                notes='Catalogue import',
                created_by=actor,
            )
            summary['stock_adjusted'] += 1
    return product


def _upsert_fulfillment(product, spec):
    fulfillment = ProductFulfillment.objects.filter(product=product).first() or ProductFulfillment(product=product)
    fulfillment.mode = spec['mode']
    fulfillment.entitlement_kind = spec['kind']
    fulfillment.template = spec.get('template', '') if spec['mode'] == ProductFulfillment.TEMPLATE else ''
    fulfillment.duration_days = spec.get('duration_days')
    fulfillment.per_unit = spec.get('per_unit', False)
    fulfillment.full_clean()
    fulfillment.save()


def write_dev_vars(path, values):
    """
    Merge `values` into a .dev.vars file, keeping every other line (a WEBHOOK_SECRET, say), and
    return the names it wrote. Written as UTF-8 without a BOM, which would break the first variable.
    The caller decides whether a key may be replaced: the import passes one only when it issued it.
    """
    path = Path(path)
    lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    index = {
        re.split(r'\s*=', line, maxsplit=1)[0].strip(): i
        for i, line in enumerate(lines)
        if '=' in line and not line.lstrip().startswith('#')
    }
    written = []
    for name, value in values.items():
        line = f'{name}={value}'
        if name in index:
            lines[index[name]] = line
        else:
            lines.append(line)
        written.append(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\n'.join(lines) + '\n').encode('utf-8'))
    return written


class Command(BaseCommand):
    help = 'Import a storefront catalogue (products, listings, variants, fulfilments) and provision its channel.'

    def add_arguments(self, parser):
        parser.add_argument('--catalog', required=True, help='Path to the catalogue JSON file.')
        parser.add_argument('--dev-vars', default=None, help='A .dev.vars file to write the channel key and base URL to.')
        parser.add_argument('--base-url', default='http://127.0.0.1:8000', help='RetailOps URL the store should call.')
        parser.add_argument('--new-key', action='store_true', help='Issue another key for an existing channel.')
        parser.add_argument('--by', default=None, help='Email of the staff user the import is attributed to.')
        parser.add_argument('--dry-run', action='store_true', help='Validate and apply, then roll everything back.')
        parser.add_argument('--allow-default-db', action='store_true',
                            help='Run against the default development database (db.sqlite3) anyway.')

    def handle(self, *args, **opts):
        if refuses_default_database() and not opts['allow_default_db']:
            raise CommandError(SCRATCH_DB_ADVICE)

        path = Path(opts['catalog'])
        try:
            document = parse_catalog(json.loads(path.read_text(encoding='utf-8')))
        except FileNotFoundError as exc:
            raise CommandError(f'No catalogue at {path}.') from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f'{path} is not valid JSON: {exc}') from exc
        except ImportProblems as exc:
            raise CommandError('The catalogue has problems:\n  - ' + '\n  - '.join(exc.problems)) from exc

        actor = self._actor(opts['by'])
        try:
            with transaction.atomic():
                summary, raw_key, channel = import_catalog(document, actor=actor, new_key=opts['new_key'])
                if opts['dry_run']:
                    transaction.set_rollback(True)
        except ValidationError as exc:
            raise CommandError('; '.join(exc.messages)) from exc

        verb = 'Would import' if opts['dry_run'] else 'Imported'
        self.stdout.write(self.style.SUCCESS(f'{verb} {path.name} for channel {channel.slug!r}.'))
        for name, value in summary.items():
            self.stdout.write(f'  {name.replace("_", " ")}: {value}')

        if raw_key and not opts['dry_run']:
            if opts['dev_vars']:
                values = {'RETAILOPS_BASE_URL': opts['base_url'], 'RETAILOPS_CHANNEL_KEY': raw_key}
                write_dev_vars(opts['dev_vars'], values)
                self.stdout.write(f'  channel key written to {opts["dev_vars"]}')
            else:
                self.stdout.write(self.style.WARNING('CHANNEL KEY (shown only once - copy now):'))
                self.stdout.write(f'    {raw_key}')
        elif opts['dev_vars'] and not opts['dry_run']:
            write_dev_vars(opts['dev_vars'], {'RETAILOPS_BASE_URL': opts['base_url']})

    def _actor(self, email):
        if email:
            actor = User.objects.filter(email=email).first()
            if actor is None:
                raise CommandError(f'No user with email {email}.')
            return actor
        actor = User.objects.filter(is_staff=True, is_active=True).order_by('pk').first()
        if actor is None:
            raise CommandError('No staff user found to attribute the import to; pass --by <email>.')
        return actor
