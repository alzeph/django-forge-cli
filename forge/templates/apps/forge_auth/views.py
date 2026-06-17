from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework import status
from rest_framework.response import Response
from rest_framework.request import Request
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework import viewsets, permissions, mixins, serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.conf import settings
from forge_auth.serializers import (
    ExistsResponseSerializer, VerifyFieldSerializer,
    ValidationError400Serializer, 
    LoginSerializer, GroupSerializer, UserSerializer,
    UserSerializer, LoginSuccessSerializer,
    RefreshSerializer, UsernameSerializer
)
from forge_auth.models import Group, OtpToken
from forge_auth.conf import forge_auth_config

User = get_user_model()

class GroupViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet
):
    """
    ViewSet qui ne permet que GET (liste et détail) sur Group.
    """
    queryset = Group.objects.all()
    serializer_class = GroupSerializer 
    permission_classes = [permissions.AllowAny]
    pagination_class = None
    

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'patch', 'put', 'delete']

    def get_permissions(self):
        public_actions = ['create', 'obtain_otp', 'verify_email', 'verify_phone', 'login']
        if self.action in public_actions:
            permission_classes = [permissions.AllowAny]
        else:
            permission_classes = [permissions.IsAuthenticated]
        return [permission() for permission in permission_classes]

    def _verify_field(self, field_name: str, value: str, exclude_value: str = None):
        """
        Méthode générique pour vérifier si une valeur existe sur un champ spécifique.
        """
        if not value:
            return Response({"detail": f"{field_name} is required"}, status=status.HTTP_400_BAD_REQUEST)

        users_qs = User.objects.all()
        if exclude_value:
            users_qs = users_qs.exclude(**{field_name: exclude_value})

        exists = users_qs.filter(**{field_name: value}).exists()
        return Response({"exists": exists})
    
    @extend_schema(
        operation_id="verify-email",
        summary="Vérifie si un email existe",
        description="Permet de vérifier si un email est déjà utilisé. Possibilité d'exclure un email existant.",
        request=VerifyFieldSerializer,
        responses={200: ExistsResponseSerializer}
    )
    @action(
        detail=False,
        methods=['post'],
        url_path=r'verify-email',
        url_name='verify-email',
        permission_classes=[permissions.AllowAny]
    )
    def verify_email(self, request: Request, pk=None):
        """
        Vérifie si un email existe, possibilité d'exclure un email.
        """
        verify_email = request.data.get('verify')
        exclude_email = request.data.get('exclude')
        return self._verify_field('email', verify_email, exclude_email)

    @extend_schema(
        operation_id='verify-phone_number',
        summary="Vérifie si un phone existe",
        description="Permet de vérifier si un phone est déjà utilisé. Possibilité d'exclure un phone existant.",
        request=VerifyFieldSerializer,
        responses={200: ExistsResponseSerializer}
    )
    @action(
        detail=False,
        methods=['post'],
        url_path=r'verify-phone',
        url_name='verify-phone',
        permission_classes=[permissions.AllowAny]
    )
    def verify_phone(self, request: Request, pk=None):
        """
        Vérifie si un numéro de téléphone existe, possibilité d'exclure un numéro.
        """
        verify_phone = request.data.get('verify')
        exclude_phone = request.data.get('exclude')
        return self._verify_field('phone_number', verify_phone, exclude_phone)

    @extend_schema(
        methods=['get'],
        operation_id="get_current_user",
        summary="Get current user",
        description="Get current user",
        responses={200: UserSerializer},
        request=None
    )
    @action(
        detail=False,
        methods=['get'],
        url_name='current',
        url_path=r'current'
    )
    def current_user(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
    
    @extend_schema(
        methods=['post'],
        operation_id="login",
        summary="Login",
        description="Login",
        request=LoginSerializer,
        responses={
            200: LoginSuccessSerializer,
            400: ValidationError400Serializer
        }
    )
    @action(detail=False, methods=['post'], url_name='login', url_path=r'login', permission_classes=[permissions.AllowAny])
    def login(self, request, *args, **kwargs):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token = RefreshToken.for_user(user)
        access = str(token.access_token)
        refresh = str(token)
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])
        
        response = Response(status=status.HTTP_200_OK)
        if forge_auth_config.jwt_conf.VIA_JSON:
            response =response.data = {"access": access, "refresh": refresh, "user": UserSerializer(user).data}
        if forge_auth_config.jwt_conf.VIA_HTTP_ONLY:
            response.set_cookie(
                key="access",
                value=access,
                httponly=True,
                secure=settings.DEBUG,
                samesite=settings.DEBUG and "Lax" or None,
                path="/",
            )

            response.set_cookie(
                key="refresh",
                value=refresh,
                httponly=True,
                secure=settings.DEBUG,
                samesite=settings.DEBUG and "Lax" or None,
                path="/",
            )
        return response
        
    @extend_schema(
        methods=['post'],
        operation_id="logout",
        summary="Logout",
        description="Logout",
        responses={204: None}
    )
    @action(detail=False, methods=['post'], url_name='logout', url_path=r'logout')
    def logout(self, request, *args, **kwargs):
        try:
            refresh = request.COOKIES.get("refresh") or request.data.get("refresh")
            if refresh:
                token = RefreshToken(refresh)
                token.blacklist()
        except Exception:
            pass

        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie("access")
        response.delete_cookie("refresh")
        return response

    @extend_schema(
        methods=['get'],
        operation_id="session_check",
        summary="Check session",
        description="Check session",
        responses={200: UserSerializer}
    )
    @action(detail=False, methods=['get'], url_name='session-check', url_path=r'session-check')
    def session_check(self, request, *args, **kwargs):
        # Si on arrive ici, le user est authentifié
        return Response(UserSerializer(request.user).data)
    
    @extend_schema(
        methods=['post'],
        operation_id="refresh",
        summary="Refresh token",
        description="Refresh token",
        request=RefreshSerializer,
        responses={200: RefreshSerializer}
    )
    @action(detail=False, methods=['post'], url_name='refresh', url_path=r'refresh')
    def refresh(self, request, *args, **kwargs):
        refresh = request.COOKIES.get("refresh") or request.data.get("refresh")
        serializer = RefreshSerializer(data={"refresh": refresh})
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)
    
    @extend_schema(
        operation_id="obtain-otp",
        summary="obtention de l'otp",
        description="obtention de l'otp de la part de l'utilisateur",
        request=UsernameSerializer,
        responses={
            200: OpenApiResponse(
                description="OTP envoyé",
                response=UserSerializer
            ),
            404: OpenApiResponse(
                description="User not found"
            ),
        },
    )
    @action(detail=False, methods=['post'], url_name='obtain-otp', url_path=r'obtain-otp', permission_classes=[permissions.AllowAny])
    def obtain_otp(self, request, *args, **kwargs):
        if "status" not in forge_auth_config.optional_fields and forge_auth_config.otp_conf.USE_OTP:
            data = UsernameSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            user = User.get(data.validated_data["username"])
            if not user:
                return Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
            otp_token, _ = OtpToken.objects.get_or_create(user=user)
            otp_token.generate_otp()
            return Response(UserSerializer(user).data) 
        return Response({"detail": "Otp already enabled"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

