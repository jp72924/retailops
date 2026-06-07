from rest_framework import serializers, status
from rest_framework.authentication import SessionAuthentication, TokenAuthentication
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Payment, SystemSettings
from core.services.bcv import BCVRateError, update_secondary_exchange_rate
from api.kiosk.authentication import KioskTokenAuthentication
from api.permissions import IsManagerOrAdmin


OCR_API_KEY_NO_CHANGE = '__no_change__'
MASKED_API_KEY = '***'
OCR_PAYMENT_METHODS = {Payment.MOBILE_PAYMENT, Payment.BANK_TRANSFER}


class SystemSettingsSerializer(serializers.ModelSerializer):
    ocr_api_key = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = SystemSettings
        fields = [
            'currency_code', 'currency_symbol', 'decimal_places',
            'secondary_currency_enabled', 'secondary_currency_code',
            'secondary_currency_symbol', 'secondary_decimal_places',
            'secondary_exchange_rate',
            'secondary_rate_auto_update_enabled', 'secondary_rate_source_url',
            'secondary_rate_source_field', 'secondary_rate_updated_at',
            'ocr_enabled', 'ocr_provider', 'ocr_base_url', 'ocr_api_key',
            'ocr_timeout_seconds', 'ocr_max_file_mb',
            'ocr_strict_amount', 'ocr_require_complete',
            'ocr_enabled_methods', 'receipt_image_required_for_receipt_methods',
            'delete_receipt_image_after_days',
        ]
        read_only_fields = ['secondary_rate_updated_at']

    def validate_secondary_exchange_rate(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('Must be greater than zero.')
        return value

    def validate_ocr_timeout_seconds(self, value):
        if value <= 0:
            raise serializers.ValidationError('Must be greater than zero.')
        return value

    def validate_ocr_max_file_mb(self, value):
        if value <= 0:
            raise serializers.ValidationError('Must be greater than zero.')
        return value

    def validate_delete_receipt_image_after_days(self, value):
        if value <= 0:
            raise serializers.ValidationError('Must be greater than zero.')
        return value

    def validate_ocr_enabled_methods(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('Must be a list of payment method names.')
        if not all(isinstance(method, str) for method in value):
            raise serializers.ValidationError('Each payment method must be a string.')
        unsupported = sorted(set(value) - OCR_PAYMENT_METHODS)
        if unsupported:
            raise serializers.ValidationError(
                f'Unsupported OCR payment method(s): {", ".join(unsupported)}.'
            )
        return value

    def validate(self, attrs):
        if attrs.get('ocr_api_key') in {OCR_API_KEY_NO_CHANGE, MASKED_API_KEY}:
            attrs.pop('ocr_api_key')

        merged = {**{
            'secondary_currency_enabled': self.instance.secondary_currency_enabled if self.instance else False,
            'secondary_currency_symbol':  self.instance.secondary_currency_symbol if self.instance else '',
            'ocr_enabled': self.instance.ocr_enabled if self.instance else False,
            'ocr_base_url': self.instance.ocr_base_url if self.instance else '',
        }, **attrs}
        if merged.get('secondary_currency_enabled') and not (merged.get('secondary_currency_symbol') or '').strip():
            raise serializers.ValidationError({
                'secondary_currency_symbol': 'Required when secondary currency is enabled.'
            })
        if merged.get('ocr_enabled') and not (merged.get('ocr_base_url') or '').strip():
            raise serializers.ValidationError({
                'ocr_base_url': 'Required when OCR is enabled.'
            })

        auto_update = attrs.get(
            'secondary_rate_auto_update_enabled',
            self.instance.secondary_rate_auto_update_enabled if self.instance else False,
        )
        source_url = attrs.get(
            'secondary_rate_source_url',
            self.instance.secondary_rate_source_url if self.instance else '',
        )
        source_field = attrs.get(
            'secondary_rate_source_field',
            self.instance.secondary_rate_source_field if self.instance else '',
        )
        if auto_update:
            if not (source_url or '').strip():
                raise serializers.ValidationError({
                    'secondary_rate_source_url': 'Required when automatic rate update is enabled.'
                })
            if not (source_field or '').strip():
                raise serializers.ValidationError({
                    'secondary_rate_source_field': 'Required when automatic rate update is enabled.'
                })
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['ocr_api_key'] = MASKED_API_KEY if instance.ocr_api_key else ''
        return data


class SystemSettingsView(APIView):
    """
    GET  /api/v1/settings/  — retrieve current system-wide settings.
    PATCH /api/v1/settings/ — update currency settings (Manager or Admin only).

    Kiosk stations authenticated via ``KioskKey`` are allowed on GET so the
    checkout app can sync the secondary-currency exchange rate from the
    same source of truth the back-office uses.
    """

    authentication_classes = [
        TokenAuthentication, SessionAuthentication, KioskTokenAuthentication,
    ]

    def get_permissions(self):
        if self.request.method in ('PATCH', 'PUT'):
            return [IsManagerOrAdmin()]
        return super().get_permissions()

    def get(self, request):
        instance = SystemSettings.get()
        serializer = SystemSettingsSerializer(instance)
        return Response(serializer.data)

    def patch(self, request):
        instance = SystemSettings.get()
        serializer = SystemSettingsSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class SecondaryRateRefreshView(APIView):
    """
    POST /api/v1/settings/secondary-rate/refresh/

    Fetch the secondary-currency exchange rate (e.g. BCV via DolarApi) from the
    configured source and persist it. Manager or Admin only. Lets external
    schedulers trigger an on-demand update without editing settings.
    """

    authentication_classes = [TokenAuthentication, SessionAuthentication]
    permission_classes = [IsManagerOrAdmin]

    def post(self, request):
        instance = SystemSettings.get()
        try:
            rate = update_secondary_exchange_rate(instance)
        except BCVRateError as exc:
            return Response(
                {'errors': [exc.message], 'code': exc.code},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response({
            'secondary_exchange_rate': str(rate),
            'secondary_rate_updated_at': instance.secondary_rate_updated_at,
        })
