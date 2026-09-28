from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0020_normalize_customer_national_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='CurrencySnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('currency_code', models.CharField(max_length=3)),
                ('currency_symbol', models.CharField(max_length=4)),
                ('decimal_places', models.PositiveSmallIntegerField()),
                ('secondary_currency_enabled', models.BooleanField()),
                ('secondary_currency_code', models.CharField(blank=True, max_length=3)),
                ('secondary_currency_symbol', models.CharField(blank=True, max_length=4)),
                ('secondary_decimal_places', models.PositiveSmallIntegerField()),
                ('secondary_exchange_rate', models.DecimalField(decimal_places=8, max_digits=20)),
                ('rate_as_of', models.DateTimeField(blank=True, null=True)),
                ('rate_source', models.CharField(choices=[('fetched', 'Fetched from the rate source'), ('manual', 'Entered manually'), ('unknown', 'Unknown'), ('backfilled', 'Approximate, assigned when rate tracking began')], max_length=20)),
                ('conversion_rule', models.CharField(default='half_up_v1', max_length=20)),
                ('fingerprint', models.CharField(editable=False, max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Currency Snapshot',
            },
        ),
        # Nullable here only so 0022 can backfill existing payments; 0023
        # makes it NOT NULL. They are separate migrations because on
        # PostgreSQL the backfill's UPDATE queues deferred FK trigger events,
        # and an ALTER TABLE on the same table in the same transaction fails.
        migrations.AddField(
            model_name='payment',
            name='currency_snapshot',
            field=models.ForeignKey(db_index=False, editable=False, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.currencysnapshot'),
        ),
        migrations.AddField(
            model_name='systemsettings',
            name='secondary_rate_source',
            field=models.CharField(choices=[('fetched', 'Fetched from the rate source'), ('manual', 'Entered manually'), ('unknown', 'Unknown')], default='unknown', max_length=20),
        ),
    ]
