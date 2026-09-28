"""
Make Payment.currency_snapshot mandatory.

0022 filled every existing row. Run migrations with the application stopped:
a payment created by pre-0021 code between 0022 and this migration would have
no snapshot, and this migration would then fail rather than guess one.
"""
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_backfill_payment_currency_snapshots'),
    ]

    operations = [
        migrations.AlterField(
            model_name='payment',
            name='currency_snapshot',
            field=models.ForeignKey(db_index=False, editable=False, on_delete=django.db.models.deletion.PROTECT, related_name='+', to='core.currencysnapshot'),
        ),
    ]
