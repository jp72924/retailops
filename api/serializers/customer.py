from rest_framework import serializers

from core.models import Customer, User


class CustomerSerializer(serializers.ModelSerializer):
    """
    Full read/write serializer for Customer.

    - full_name: computed from first_name + last_name (read-only convenience field).
    - user: optional FK to a User account; accepted as a primary key on write,
      returned as a primary key on read (not nested, since customer↔user is a
      loose association, not a composition).
    """
    full_name = serializers.SerializerMethodField()
    user = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        required=False,
        allow_null=True,
        default=None,
    )

    class Meta:
        model  = Customer
        fields = [
            'id', 'full_name',
            'first_name', 'last_name', 'email', 'phone',
            'national_id', 'date_of_birth', 'gender',
            'address_line1', 'address_line2',
            'city', 'state', 'postal_code', 'country',
            'notes', 'user',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'full_name', 'created_at', 'updated_at']

    def get_full_name(self, obj) -> str:
        return obj.get_full_name()

    def validate_email(self, value):
        qs = Customer.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A customer with this email already exists.'
            )
        return value

    def validate_national_id(self, value):
        if not value:
            return value
        qs = Customer.objects.filter(national_id=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                'A customer with this national ID already exists.'
            )
        return value
