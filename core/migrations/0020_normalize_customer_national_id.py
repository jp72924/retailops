"""
Normalize stored national IDs.

Customer.save() now normalizes national_id, which is what makes the column's
existing unique=True mean *normalized* uniqueness. Rows written before that
change may still carry punctuation ("V-12.345.678"), which would be invisible
to a normalized lookup -- the kiosk would return 404 for a customer that
exists.

The forward migration fails loudly if two rows normalize to the same value.
That state was reachable before this release (raw uniqueness let "V123456" and
"V-123456" coexist), and it is not this migration's place to decide which of
two real people to keep. The error names the offending rows so an operator can
merge them by hand and re-run.
"""
import re

from django.db import migrations


def _normalize(value):
    # Mirrors core.services.receipt_matching.normalize_document_id, inlined
    # because migrations must not depend on application code that can change.
    return re.sub(r'[^0-9A-Za-z]+', '', str(value or '')).upper()


def normalize_national_ids(apps, schema_editor):
    Customer = apps.get_model('core', 'Customer')

    rows = list(
        Customer.objects
        .exclude(national_id__isnull=True)
        .exclude(national_id='')
        .values_list('pk', 'national_id')
    )

    normalized_to_pks = {}
    for pk, raw in rows:
        normalized_to_pks.setdefault(_normalize(raw), []).append((pk, raw))

    collisions = {
        norm: entries for norm, entries in normalized_to_pks.items() if len(entries) > 1
    }
    if collisions:
        detail = '; '.join(
            '%s <- %s' % (norm, ', '.join('pk=%s (%r)' % e for e in entries))
            for norm, entries in sorted(collisions.items())
        )
        raise RuntimeError(
            'Cannot normalize national IDs: %d value(s) collide once punctuation '
            'is removed. Merge or correct these customers, then re-run the '
            'migration. %s' % (len(collisions), detail)
        )

    for norm, ((pk, raw),) in normalized_to_pks.items():
        if raw != norm:
            Customer.objects.filter(pk=pk).update(national_id=norm)

    # Collapse '' to NULL so the unique index does not treat blanks as equal.
    Customer.objects.filter(national_id='').update(national_id=None)


def noop_reverse(apps, schema_editor):
    """
    Irreversible in substance: the original punctuation is not recorded, so it
    cannot be restored. Declared as a no-op rather than raising so that
    unrelated rollbacks are not blocked by it.
    """


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_enforce_order_line_and_national_id_rules'),
    ]

    operations = [
        migrations.RunPython(normalize_national_ids, noop_reverse),
    ]
