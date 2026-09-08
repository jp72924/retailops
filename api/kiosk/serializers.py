from rest_framework import serializers

from core.models import Customer, Payment, Product, RecipientProfile
from core.services.customers import check_national_id
from core.services.receipt_matching import normalize_document_id
from api.serializers.product import product_image_url


class KioskIdentifySerializer(serializers.Serializer):
    national_id = serializers.CharField(min_length=6, max_length=20)

    def validate_national_id(self, value):
        """
        Normalize so a shopper finds themselves however they type the cedula.

        The previous comment claimed this stripped dots and dashes; it only
        called strip(), so a customer stored as "V-12.345.678" was a 404 for
        anyone typing "V12345678". min_length runs on the raw value first, so
        a too-short entry is still rejected before it gets here.
        """
        return normalize_document_id(value)


class KioskIdentifyResponseSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    first_name = serializers.CharField()
    last_name = serializers.CharField()


class KioskRegisterSerializer(serializers.Serializer):
    national_id  = serializers.CharField(min_length=6, max_length=20)
    first_name   = serializers.CharField(max_length=150)
    last_name    = serializers.CharField(max_length=150)
    email        = serializers.EmailField(max_length=254)
    phone        = serializers.CharField(max_length=30)
    date_of_birth = serializers.DateField()
    gender       = serializers.ChoiceField(choices=['M', 'F'])
    state        = serializers.CharField(max_length=100)
    city         = serializers.CharField(max_length=100)

    def validate_national_id(self, value):
        """
        Same rule the API and the back office apply: unique under
        normalization, so the kiosk cannot register a second record for
        someone who already exists under a different spelling.
        """
        try:
            return check_national_id(value)
        except ValueError:
            raise serializers.ValidationError(
                'A customer with this ID already exists.',
                code='duplicate_national_id',
            )

    def validate_email(self, value):
        if Customer.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                'Este correo ya está registrado.',
                code='duplicate_email',
            )
        return value

    def create(self, validated_data):
        return Customer.objects.create(
            national_id=validated_data['national_id'],
            first_name=validated_data['first_name'],
            last_name=validated_data['last_name'],
            email=validated_data['email'],
            phone=validated_data['phone'],
            date_of_birth=validated_data['date_of_birth'],
            gender=validated_data['gender'],
            state=validated_data['state'],
            city=validated_data['city'],
            country='Venezuela',
        )


class KioskProductSerializer(serializers.ModelSerializer):
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = ['id', 'sku', 'name', 'unit_price', 'current_stock',
                  'is_out_of_stock', 'is_low_stock', 'is_active', 'image_url']

    current_stock  = serializers.SerializerMethodField()
    is_out_of_stock = serializers.SerializerMethodField()
    is_low_stock    = serializers.SerializerMethodField()

    def get_current_stock(self, obj):
        return obj.current_stock

    def get_is_out_of_stock(self, obj):
        return obj.is_out_of_stock

    def get_is_low_stock(self, obj):
        return obj.is_low_stock

    def get_image_url(self, obj):
        return product_image_url(obj, self.context.get('request'))


class KioskRecipientProfileSerializer(serializers.ModelSerializer):
    """
    Customer-safe view of a recipient profile — only what a shopper needs on
    screen to send the payment. Deliberately narrower than the Manager+
    RecipientProfileSerializer, which also carries label, created_by and the
    internal flags. phone and account_number are both always present; exactly
    one is populated for a given payment method (see RecipientProfile.clean).
    """
    payment_method_display = serializers.CharField(
        source='get_payment_method_display', read_only=True,
    )

    class Meta:
        model = RecipientProfile
        fields = ['payment_method', 'payment_method_display', 'bank',
                  'phone', 'account_number', 'document_id']


class KioskCheckoutItemSerializer(serializers.Serializer):
    sku = serializers.CharField(max_length=100)
    quantity = serializers.IntegerField(min_value=1)


class KioskCheckoutSerializer(serializers.Serializer):
    customer_id = serializers.IntegerField()
    items = KioskCheckoutItemSerializer(many=True, min_length=1)
    payment_reference = serializers.CharField(max_length=100)
    payment_method = serializers.ChoiceField(
        choices=[choice[0] for choice in Payment.METHOD_CHOICES],
        default=Payment.CARD,
    )
    receipt = serializers.JSONField(required=False)

    def validate_items(self, value):
        # Deduplicate: merge items with the same SKU.
        #
        # Deliberately different from the back office and the REST API, which
        # reject a repeated product. At a self-checkout, scanning the same item
        # twice means quantity two -- turning that into an error would be a
        # regression. Both surfaces end with the same invariant: one line per
        # product on the saved order.
        merged = {}
        for item in value:
            sku = item['sku']
            if sku in merged:
                merged[sku]['quantity'] += item['quantity']
            else:
                merged[sku] = dict(item)
        return list(merged.values())

    def validate_receipt(self, value):
        if value in (None, ''):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError('Receipt must be an object.')
        return value


class KioskReceiptItemSerializer(serializers.Serializer):
    product_name = serializers.CharField()
    sku = serializers.CharField()
    quantity = serializers.IntegerField()
    unit_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    line_total = serializers.DecimalField(max_digits=12, decimal_places=2)


class KioskReceiptSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    customer_name = serializers.CharField()
    items = KioskReceiptItemSerializer(many=True)
    subtotal = serializers.DecimalField(max_digits=12, decimal_places=2)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    total_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    payment_method = serializers.CharField()
    payment_number = serializers.CharField()
    payment_reference = serializers.CharField()
    station_number = serializers.IntegerField()
    store_identifier = serializers.CharField()
    created_at = serializers.DateTimeField()
