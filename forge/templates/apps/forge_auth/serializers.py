from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from rest_framework import serializers, exceptions
from django.db import transaction
from rest_framework_simplejwt.tokens import RefreshToken
from forge_auth.conf import forge_auth_config

User = get_user_model()


class GroupSerializer(serializers.ModelSerializer):
    class Meta:
        model = Group
        fields = ['pk', 'name', 'permissions']
        
class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ['pk', 'name', 'content_type', 'codename']

global_fields = [
    'pk', 'first_name', 'last_name', 'phone_number', 'email',
    'last_login', 'is_staff', 'is_active', 'is_superuser',
    'groups', 'groups_detail', 'user_permissions', 'date_joined',
] 
class UserSerializer(serializers.ModelSerializer):
    groups_detail = GroupSerializer(source='groups', many=True, read_only=True)
    user_permissions = PermissionSerializer(many=True, read_only=True)
    class Meta:
        model = User
        fields = global_fields + ['status'] if 'status'  not in forge_auth_config.optional_fields else global_fields
        
        extra_kwargs = {
            'pk': {'read_only': True},
            'last_login': {'read_only': True},
            'is_staff': {'read_only': True},
            'is_active': {'read_only': True},
            'is_superuser': {'read_only': True},
            'user_permissions': {'read_only': True},
            'date_joined': {'read_only': True},
            'first_name': {'required': False},
            'last_name': {'required': False},
        }

    def create(self, validated_data):
        with transaction.atomic():
            user = User.objects.create_user(**validated_data)
            return user
        
class UsernameSerializer(serializers.Serializer):
    username = serializers.CharField()

    def validate_username(self, value):
        username_field = forge_auth_config.username_field
        if not value:
            raise serializers.ValidationError("username est obligatoire")
        if forge_auth_config.register_include_in_otp:
            User.objects.get_or_create(**{username_field: value})
            return value
        if User.objects.filter(**{username_field: value}).exists():
            return value
        raise serializers.ValidationError("L'utilisateur n'existe pas")
    
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(required=False)
    code = serializers.CharField(required=False)
    
    def validate(self, attrs):
        
        """
        permet de verifier l'authentification
        """
        
        attrs = super().validate(attrs)
        
        username = attrs.get('username')
        code = attrs.get('code')
        password = attrs.get('password')
        
        user = User.get(username)
        if 'status' not in forge_auth_config.optional_fields and forge_auth_config.otp_conf.USE_OTP:
            if not code:
                raise exceptions.AuthenticationFailed("Code OTP obligatoire")
            otp_token = user.otp_token
            if not otp_token.verify_otp(code):
                raise exceptions.AuthenticationFailed("Code incorrect")
            
        else:
            if not password:
                raise exceptions.AuthenticationFailed("Mot de passe obligatoire")
            if not user.check_password(password):
                raise exceptions.AuthenticationFailed("Mot de passe incorrect")
        attrs['user'] = user
        return attrs

class LoginSuccessSerializer(serializers.Serializer):
    user = UserSerializer()
    access = serializers.CharField()
    refresh = serializers.CharField()
    
class RefreshSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=True)
    access = serializers.CharField(required=False, read_only=True)
    
    def validate(self, attrs):
        attrs = super().validate(attrs)
        refresh = attrs.get('refresh')
        token = RefreshToken(refresh)
        attrs['access'] = str(token.access_token)
        return attrs


class ExistsResponseSerializer(serializers.Serializer):
    exists = serializers.BooleanField()

class VerifyFieldSerializer(serializers.Serializer):
    verify = serializers.EmailField()
    exclude = serializers.EmailField(required=False)

class NotFound404ResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()
    
class DetailResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()

class ValidationError400Serializer(serializers.Serializer):
    field_name = serializers.ListField(
        child=serializers.CharField(),
        help_text="Liste des messages d'erreur liés à ce champ."
    )
   
class ResultResponseSerializer(serializers.Serializer):
    result = serializers.BooleanField()

 