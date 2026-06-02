from django.contrib.auth.forms import PasswordResetForm
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_str
from django.utils.http import urlsafe_base64_decode
from drf_spectacular.utils import extend_schema, inline_serializer
from drf_spectacular.openapi import OpenApiTypes
from rest_framework import serializers, status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from django.contrib.auth import authenticate, get_user_model
from api.throttling import LoginRateThrottle, PasswordResetRateThrottle

User = get_user_model()


# ── Token serializer ──────────────────────────────────────────────────────────

class TokenObtainSerializer(serializers.Serializer):
    """
    Accepts email + password.

    The custom User model uses email as USERNAME_FIELD, so DRF's built-in
    ObtainAuthToken (which expects a 'username' field) cannot be used directly.
    This serializer passes email as the 'username' argument to authenticate(),
    which is correct because Django's ModelBackend looks up by USERNAME_FIELD.
    """
    email    = serializers.EmailField()
    password = serializers.CharField(write_only=True, style={'input_type': 'password'})

    def validate(self, data):
        email    = data.get('email')
        password = data.get('password')

        user = authenticate(
            request=self.context.get('request'),
            username=email,   # ModelBackend maps this to USERNAME_FIELD (email)
            password=password,
        )

        if user is None:
            raise serializers.ValidationError(
                'Invalid email or password.',
                code='invalid_credentials',
            )

        if not user.is_active:
            raise serializers.ValidationError(
                'This account has been deactivated.',
                code='account_disabled',
            )

        data['user'] = user
        return data


# ── Views ─────────────────────────────────────────────────────────────────────

@extend_schema(
    request=TokenObtainSerializer,
    responses={200: inline_serializer(
        name='TokenResponse',
        fields={
            'token':     serializers.CharField(),
            'user_id':   serializers.IntegerField(),
            'email':     serializers.EmailField(),
            'role_name': serializers.CharField(allow_null=True),
        },
    )},
    description='Obtain an authentication token by supplying email and password.',
    tags=['auth'],
)
class ObtainTokenView(APIView):
    """
    POST /api/v1/auth/token/

    Returns an authentication token for the given credentials.

    Request body:
        {"email": "user@example.com", "password": "secret"}

    Response 200:
        {
            "token":     "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
            "user_id":   3,
            "email":     "user@example.com",
            "role_name": "Manager"
        }
    """
    permission_classes = [AllowAny]
    throttle_classes   = [LoginRateThrottle]     # 20/min per IP; see api/throttling.py
    serializer_class   = TokenObtainSerializer   # used by the browsable API

    def post(self, request):
        serializer = self.serializer_class(
            data=request.data,
            context={'request': request},
        )
        serializer.is_valid(raise_exception=True)

        user  = serializer.validated_data['user']
        token, _ = Token.objects.get_or_create(user=user)

        return Response({
            'token':     token.key,
            'user_id':   user.pk,
            'email':     user.email,
            'role_name': user.role.name if user.role else None,
        })


@extend_schema(
    request=None,
    responses={200: inline_serializer(
        name='MeResponse',
        fields={
            'user_id':    serializers.IntegerField(),
            'email':      serializers.EmailField(),
            'first_name': serializers.CharField(),
            'last_name':  serializers.CharField(),
            'role_name':  serializers.CharField(allow_null=True),
            'is_active':  serializers.BooleanField(),
        },
    )},
    description='Return the authenticated caller\'s identity. Used by clients to verify a token and surface who is logged in.',
    tags=['auth'],
)
class MeView(APIView):
    """
    GET /api/v1/auth/me/

    Returns the identity of the user whose token was used to authenticate
    the request. Useful for CLI/UI clients that need to display "logged in
    as ..." or to verify a stored token is still valid.

    Response 200:
        {
            "user_id":    3,
            "email":      "manager@retailops.local",
            "first_name": "Maria",
            "last_name":  "Manager",
            "role_name":  "Manager",
            "is_active":  true
        }

    Response 401 if the token is missing or invalid.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            'user_id':    user.pk,
            'email':      user.email,
            'first_name': user.first_name,
            'last_name':  user.last_name,
            'role_name':  user.role.name if user.role else None,
            'is_active':  user.is_active,
        })


@extend_schema(
    request=None,
    responses={204: None},
    description='Delete the caller\'s authentication token (logout). Client should discard the token.',
    tags=['auth'],
)
class RevokeTokenView(APIView):
    """
    POST /api/v1/auth/token/revoke/

    Deletes the caller's authentication token (logout).
    The client should discard the token after calling this endpoint.

    Response 204: No Content
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            request.user.auth_token.delete()
        except Token.DoesNotExist:
            # Token already absent — treat as success.
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Password reset serializers ────────────────────────────────────────────────

class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    uid          = serializers.CharField()
    token        = serializers.CharField()
    new_password = serializers.CharField(
        write_only=True,
        min_length=8,
        style={'input_type': 'password'},
    )

    def validate(self, data):
        # Decode the base64 uid and load the user.
        try:
            pk   = force_str(urlsafe_base64_decode(data['uid']))
            user = User.objects.get(pk=pk)
        except (ValueError, TypeError, OverflowError, User.DoesNotExist):
            raise serializers.ValidationError(
                {'uid': 'Invalid or expired password reset link.'},
                code='invalid_token',
            )

        # Verify the HMAC token against the user's current state.
        if not default_token_generator.check_token(user, data['token']):
            raise serializers.ValidationError(
                {'token': 'Invalid or expired password reset link.'},
                code='invalid_token',
            )

        data['user'] = user
        return data


# ── Password reset views ──────────────────────────────────────────────────────

_RESET_DONE_RESPONSE = inline_serializer(
    name='PasswordResetRequestResponse',
    fields={'detail': serializers.CharField()},
)
_RESET_CONFIRM_RESPONSE = inline_serializer(
    name='PasswordResetConfirmResponse',
    fields={'detail': serializers.CharField()},
)


@extend_schema(
    request=PasswordResetRequestSerializer,
    responses={200: _RESET_DONE_RESPONSE},
    description=(
        'Request a password reset email. '
        'Always returns 200 — the response does not reveal whether the address is registered.'
    ),
    tags=['auth'],
)
class PasswordResetRequestView(APIView):
    """
    POST /api/v1/auth/password-reset/

    Generates a password-reset token and sends an email with a reset link.

    Request body:
        {"email": "user@retailops.local"}

    Response 200 (always, regardless of whether the email exists):
        {"detail": "If that email is registered, a password reset link has been sent."}

    The link in the email points to the HTML reset-confirm page:
        /password-reset/confirm/<uidb64>/<token>/

    API clients can extract the uid and token from that URL and call
    POST /api/v1/auth/password-reset/confirm/ directly instead.
    """
    permission_classes = [AllowAny]
    throttle_classes   = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        form = PasswordResetForm({'email': serializer.validated_data['email']})
        if form.is_valid():
            form.save(
                request=request._request,
                use_https=request.is_secure(),
                token_generator=default_token_generator,
                email_template_name='core/password_reset_email.txt',
                subject_template_name='core/password_reset_subject.txt',
            )

        # Unconditional 200 — never reveal whether the address is registered.
        return Response(
            {'detail': 'If that email is registered, a password reset link has been sent.'}
        )


@extend_schema(
    request=PasswordResetConfirmSerializer,
    responses={
        200: _RESET_CONFIRM_RESPONSE,
        400: inline_serializer(
            name='PasswordResetConfirmError',
            fields={'token': serializers.ListField(child=serializers.CharField())},
        ),
    },
    description=(
        'Confirm a password reset by supplying the uid and token from the reset email, '
        'plus the new password. Invalidates the user\'s existing auth token on success.'
    ),
    tags=['auth'],
)
class PasswordResetConfirmView(APIView):
    """
    POST /api/v1/auth/password-reset/confirm/

    Validates the uid + token from the reset email and sets the new password.

    Request body:
        {
            "uid":          "MQ",
            "token":        "abc-123def456",
            "new_password": "NewSecurePass1!"
        }

    Response 200:
        {"detail": "Password has been reset. Please log in with your new password."}

    On success the user's existing auth token is deleted so they must
    re-authenticate with POST /api/v1/auth/token/ to get a fresh token.

    Response 400 if the uid/token is invalid or expired.
    """
    permission_classes = [AllowAny]
    throttle_classes   = [PasswordResetRateThrottle]

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data['user']
        user.set_password(serializer.validated_data['new_password'])
        user.save()

        # Invalidate any existing API token so the caller must re-authenticate.
        Token.objects.filter(user=user).delete()

        return Response(
            {'detail': 'Password has been reset. Please log in with your new password.'}
        )
