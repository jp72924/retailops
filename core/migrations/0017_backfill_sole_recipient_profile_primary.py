from django.db import migrations
from django.db.models import Count


def promote_sole_profiles(apps, schema_editor):
    """
    Apply the lone-profile rule to data that predates it: any payment method
    with exactly one profile and no primary gets that profile promoted.
    Methods with several profiles are left alone — the choice is a human one.
    """
    RecipientProfile = apps.get_model('core', 'RecipientProfile')
    sole_methods = (
        RecipientProfile.objects.values('payment_method')
        .annotate(total=Count('id'))
        .filter(total=1)
        .values_list('payment_method', flat=True)
    )
    RecipientProfile.objects.filter(
        payment_method__in=list(sole_methods), is_primary=False,
    ).update(is_primary=True)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0016_recipientprofile_is_primary_and_more'),
    ]

    operations = [
        migrations.RunPython(promote_sole_profiles, migrations.RunPython.noop),
    ]
