from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed


class StaffTokenAuthentication(TokenAuthentication):
    """
    Token authentication for people.

    A kiosk or a sales channel is a service account: it authenticates with its
    own key, only in its own namespace, and never holds a staff token. A token
    that somehow belongs to one is refused here, so a service account can never
    reach the staff API through the endpoints that accept any authenticated
    user, whatever password or token it was given.

    The role is joined in the same query that finds the token, so this costs
    nothing and spares the permission checks that read it a second one.
    """

    def authenticate_credentials(self, key):
        model = self.get_model()
        try:
            token = model.objects.select_related('user__role').get(key=key)
        except model.DoesNotExist:
            raise AuthenticationFailed(_('Invalid token.'))

        user = token.user
        if not user.is_active:
            raise AuthenticationFailed(_('User inactive or deleted.'))
        if user.is_service:
            raise AuthenticationFailed(_('Invalid token.'))
        return user, token
