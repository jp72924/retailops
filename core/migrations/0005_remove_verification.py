from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_kiosk_integration_v2'),
    ]

    operations = [
        # ── Objective 1: remove employee-verification mechanism ──────────────────

        # VerificationRequest must be deleted before removing the FK field it
        # references on KioskStation (verification_rate is not a FK, but deleting
        # the model first keeps the order clean and mirrors the plan).
        migrations.DeleteModel(
            name='VerificationRequest',
        ),

        migrations.RemoveField(
            model_name='kioskstation',
            name='verification_rate',
        ),

        # ── Objective 3: add new Customer profile fields ─────────────────────────

        migrations.AddField(
            model_name='customer',
            name='date_of_birth',
            field=models.DateField(blank=True, null=True),
        ),

        migrations.AddField(
            model_name='customer',
            name='gender',
            field=models.CharField(
                blank=True,
                choices=[('M', 'Masculino'), ('F', 'Femenino')],
                default='',
                max_length=1,
            ),
        ),
    ]
