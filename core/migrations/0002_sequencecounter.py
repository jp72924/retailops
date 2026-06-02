from collections import defaultdict

from django.db import migrations, models


def initialize_counters(apps, schema_editor):
    """
    Scan all existing SalesOrder and Payment numbers and create a
    SequenceCounter row for each date-prefix so that new numbers continue
    from the highest value already issued.

    Number formats:
        SalesOrder  →  SO-YYYYMMDD-NNNN   → prefix  SO-YYYYMMDD
        Payment     →  PAY-YYYYMMDD-NNNN  → prefix  PAY-YYYYMMDD
    """
    SequenceCounter = apps.get_model('core', 'SequenceCounter')
    SalesOrder      = apps.get_model('core', 'SalesOrder')
    Payment         = apps.get_model('core', 'Payment')

    max_seq = defaultdict(int)

    for number in SalesOrder.objects.values_list('order_number', flat=True):
        if number:
            prefix, _, seq_str = number.rpartition('-')
            try:
                max_seq[prefix] = max(max_seq[prefix], int(seq_str))
            except ValueError:
                pass

    for number in Payment.objects.values_list('payment_number', flat=True):
        if number:
            prefix, _, seq_str = number.rpartition('-')
            try:
                max_seq[prefix] = max(max_seq[prefix], int(seq_str))
            except ValueError:
                pass

    for prefix, last_value in max_seq.items():
        SequenceCounter.objects.get_or_create(
            prefix=prefix,
            defaults={'last_value': last_value},
        )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='SequenceCounter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('prefix', models.CharField(max_length=30, unique=True)),
                ('last_value', models.PositiveIntegerField(default=0)),
            ],
            options={
                'verbose_name': 'Sequence Counter',
            },
        ),
        migrations.RunPython(initialize_counters, migrations.RunPython.noop),
    ]
