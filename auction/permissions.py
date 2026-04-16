from rest_framework.permissions import BasePermission


class IsManagerPermission(BasePermission):
    """Only authenticated users with role='manager' can access."""
    message = 'Only managers are allowed to perform this action.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'manager'
        )


class IsCaptainPermission(BasePermission):
    """Only authenticated users with role='captain' can access."""
    message = 'Only captains are allowed to perform this action.'

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'captain'
        )


class IsManagerOrReadOnly(BasePermission):
    """Managers can write; authenticated users can read."""
    def has_permission(self, request, view):
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return request.user and request.user.is_authenticated
        return (
            request.user
            and request.user.is_authenticated
            and request.user.role == 'manager'
        )
