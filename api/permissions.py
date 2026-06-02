from rest_framework.permissions import BasePermission


def role_permission(*roles):
    """
    Returns a DRF permission class that allows access only to authenticated
    users whose role.name is in `roles`.

    Mirrors core.decorators.role_required exactly.

    Usage in a ViewSet:
        permission_classes = [IsAuthenticated, role_permission('Manager', 'Admin')]

    Or via the named shortcuts below:
        permission_classes = [IsAuthenticated, IsAdminRole]
    """
    class RolePermission(BasePermission):
        allowed_roles = roles

        def has_permission(self, request, view):
            # Guard against unauthenticated requests and nullable role FK.
            return (
                request.user is not None
                and request.user.is_authenticated
                and request.user.role is not None
                and request.user.role.name in self.allowed_roles
            )

    # Give the class a meaningful name for debugging and DRF's browsable API.
    RolePermission.__name__ = 'Is' + 'Or'.join(roles)
    RolePermission.__qualname__ = RolePermission.__name__
    return RolePermission


# ── Named shortcuts ───────────────────────────────────────────────────────────
# Use these in permission_classes lists instead of calling role_permission()
# inline, so the browsable API and error messages show a readable class name.

IsAdminRole      = role_permission('Admin')
IsManagerOrAdmin = role_permission('Manager', 'Admin')
IsStaffOrAbove   = role_permission('Staff', 'Manager', 'Admin')
