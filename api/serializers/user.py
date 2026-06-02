from rest_framework import serializers

from core.models import Role, User
from .role import RoleSerializer


class UserReadSerializer(serializers.ModelSerializer):
    """
    Full read-only representation of a User, including a nested role object.
    Used for list and retrieve responses.
    Password and internal Django flags (last_login, is_superuser) are excluded.
    """
    role      = RoleSerializer(read_only=True)
    role_name = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model  = User
        fields = [
            'id', 'email', 'first_name', 'last_name',
            'role', 'role_name',
            'is_active', 'is_staff',
            'timezone', 'language',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields


class UserWriteSerializer(serializers.ModelSerializer):
    """
    Writable serializer used for user creation (invite) and profile updates.

    - password: write-only, required on create, not accepted on update
      (password changes go through the /change-password/ action).
    - role: accepted as a primary key; validated against existing roles.
    - email uniqueness is enforced at both the model level and here.
    """
    role = serializers.PrimaryKeyRelatedField(
        queryset=Role.objects.all(),
        required=True,
    )
    password = serializers.CharField(
        write_only=True,
        required=False,
        min_length=8,
        style={'input_type': 'password'},
    )

    class Meta:
        model  = User
        fields = [
            'id', 'email', 'first_name', 'last_name',
            'role', 'is_active', 'password',
            'timezone', 'language',
        ]
        read_only_fields = ['id']
        extra_kwargs = {
            'email':      {'required': True},
            'first_name': {'required': True},
            'last_name':  {'required': True},
            'timezone':   {'required': False},
            'language':   {'required': False},
        }

    def validate_email(self, value):
        qs = User.objects.filter(email__iexact=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value.lower()

    def validate(self, data):
        # Password is required only on creation.
        if self.instance is None and not data.get('password'):
            raise serializers.ValidationError(
                {'password': 'A password is required when creating a user.'}
            )
        return data

    def create(self, validated_data):
        password = validated_data.pop('password')
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user

    def update(self, instance, validated_data):
        # Password is never updated through this serializer.
        validated_data.pop('password', None)

        # Guard: prevent an admin from accidentally deactivating their own
        # account via a bulk PATCH if the view doesn't already block it.
        # (The view enforces this too, but defence-in-depth.)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


class ChangePasswordSerializer(serializers.Serializer):
    """
    Used exclusively by the /users/<id>/change-password/ action.
    """
    new_password     = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'},
    )
    confirm_password = serializers.CharField(
        write_only=True,
        style={'input_type': 'password'},
    )

    def validate(self, data):
        if data['new_password'] != data['confirm_password']:
            raise serializers.ValidationError(
                {'confirm_password': 'Passwords do not match.'}
            )
        return data
