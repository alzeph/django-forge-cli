import pyotp

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import (
    AbstractBaseUser,
    BaseUserManager,
    Group,
    Permission,
    PermissionsMixin,
)
from django.core.validators import validate_email
from django.db import models
from django.utils.translation import gettext_lazy as _

from forge_auth.conf import forge_auth_config


class UserManager(BaseUserManager):
    """Manager personnalisé : authentification par numéro de téléphone."""

    def create_user(self, password=None, **extra_fields):
        username_field = forge_auth_config.username_field
        username = extra_fields.get(username_field, None)
        if not username:
            raise ValueError(_(f"Le {username_field} est obligatoire."))
        groups = extra_fields.pop("groups", None)
        permissions = extra_fields.pop("user_permissions", None)
        user = self.model(**extra_fields)

        if groups:
            user.groups.set(groups)
        if permissions:
            user.user_permissions.set(permissions)

        user.set_password(password)
        user.save()
        return user

    def create_superuser(self, password=None, **extra_fields):
        username_field = forge_auth_config.username_field
        username = extra_fields.get(username_field, None)
        if not username:
            raise ValueError(_(f"Le {username_field} est obligatoire."))
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        return self.create_user(password, **extra_fields)


class OtpSecretMixin(models.Model):
    """
    Ajoute le champ `otp_secret` au modèle User.

    Ce mixin est inclus uniquement si `otp_secret` n'est PAS dans
    `OPTIONAL_FIELDS`. Le secret est généré automatiquement via
    pyotp et n'est pas modifiable depuis l'interface d'administration.
    """

    otp_secret = models.CharField(
        max_length=32,
        default=pyotp.random_base32,
        editable=False,
        null=False,
        verbose_name=_("Secret OTP"),
        help_text=_(
            "Clé secrète TOTP de l'utilisateur (générée automatiquement)."),
    )

    class Meta:
        abstract = True


class StatusMixin(models.Model):
    """
    Ajoute le champ ``status`` et les propriétés associées au modèle User.

    Ce mixin est inclus uniquement si "status" n'est PAS dans
    SCB_AUTH["OPTIONAL_FIELDS"].
    """

    class StatusVerified(models.TextChoices):
        UNVERIFIED = "unverified",  _("Non vérifié")
        VERIFIED = "verified",    _("Vérifié")
        BLOCKED = "blocked",     _("Bloqué")
        SUSPENDED = "suspended",   _("Suspendu")
        DELETED = "deleted",     _("Supprimé")
        DEACTIVATED = "deactivated", _("Désactivé")

    status = models.CharField(
        max_length=20,
        choices=StatusVerified.choices,
        default=StatusVerified.UNVERIFIED,
        verbose_name=_("Statut de vérification"),
    )

    # --- propriétés pratiques -----------------------------------------------

    @property
    def is_verified(self) -> bool:
        """True si le compte a été vérifié."""
        return self.status == self.StatusVerified.VERIFIED

    @property
    def is_unauthorized(self) -> bool:
        """True si le compte est bloqué, suspendu, supprimé ou désactivé."""
        return self.status in (
            self.StatusVerified.BLOCKED,
            self.StatusVerified.SUSPENDED,
            self.StatusVerified.DELETED,
            self.StatusVerified.DEACTIVATED,
        )
    
    def delete_user(self):
        self.status = self.StatusVerified.DELETED
        self.save(update_fields=["status"])
    
    def deactivate_user(self):
        self.status = self.StatusVerified.DEACTIVATED
        self.save(update_fields=["status"])

    def mark_as_verified(self):
        self.status = self.StatusVerified.VERIFIED
        self.save(update_fields=["status"])
    
    def mark_as_unverified(self):
        self.status = self.StatusVerified.UNVERIFIED
        self.save(update_fields=["status"])

    def mark_as_suspended(self):
        self.status = self.StatusVerified.SUSPENDED
        self.save(update_fields=["status"])

    class Meta:
        abstract = True


def _build_user_bases() -> tuple:
    """
    Construit la liste des classes parentes de User en fonction des champs
    activés dans SCB_AUTH["OPTIONAL_FIELDS"].

    Retourne un tuple de classes prêt à être utilisé comme bases de User.
    """
    bases: list = [AbstractBaseUser, PermissionsMixin]

    if "otp_secret" not in forge_auth_config.optional_fields:
        bases.insert(0, OtpSecretMixin)

    if "status" not  in forge_auth_config.optional_fields:
        bases.insert(0, StatusMixin)

    return tuple(bases)


class User(*_build_user_bases()):
    """
    Modèle utilisateur principal de scb_auth.

    L'authentification se fait (USERNAME_FIELD).

    Champs toujours présents
    ------------------------
    first_name, last_name, phone_number, email, password,
    last_login, is_staff, is_active, is_superuser,
    groups, user_permissions, date_joined.

    Champs conditionnels (désactivables via SCB_AUTH["OPTIONAL_FIELDS"])
    --------------------------------------------------------------------
    otp_secret       – secret TOTP (via OtpSecretMixin)
    status           – statut de vérification (via StatusMixin)
    """
    username_field = forge_auth_config.username_field

    first_name = models.CharField(max_length=30, null=True, blank=True, verbose_name=_("Prénom"))
    last_name  = models.CharField(max_length=30, null=True, blank=True, verbose_name=_("Nom"))
    phone_number = models.CharField(
        max_length=20,
        unique= username_field == "phone_number",
        null= username_field != "phone_number",
        blank= username_field != "phone_number",
        verbose_name=_("Numéro de téléphone"),
    )
    email = models.EmailField(
        unique=username_field == "email",
        blank=username_field != "email",
        null=username_field != "email",
        verbose_name=_("Adresse e-mail"),
    )
    password = models.CharField(max_length=128, blank=True, null=True, verbose_name=_("Mot de passe"))
    last_login  = models.DateTimeField(null=True, blank=True)
    is_staff    = models.BooleanField(default=False)
    is_active   = models.BooleanField(default=True)
    is_superuser = models.BooleanField(default=False)
    groups = models.ManyToManyField(Group, blank=True, verbose_name=_("Groupes"))
    user_permissions = models.ManyToManyField(
        Permission, blank=True, verbose_name=_("Permissions")
    )
    date_joined = models.DateTimeField(auto_now_add=True, verbose_name=_("Date d'inscription"))
    objects = UserManager()
    USERNAME_FIELD  = forge_auth_config.username_field
    REQUIRED_FIELDS = ["first_name", "last_name"]

    @property
    def username(self):
        return getattr(self, self.USERNAME_FIELD)

    @property
    def full_name(self) -> str:
        """Retourne le nom complet « Prénom Nom »."""
        return f"{self.first_name} {self.last_name}"

    @property
    def is_valid_email(self) -> bool:
        """True si l'adresse e-mail est syntaxiquement valide."""
        try:
            validate_email(self.email)
            return True
        except Exception:
            return False
    
    @property
    def is_valid_phone_number(self) -> bool:
        """True si le numéro de téléphone est syntaxiquement valide."""
        try:
            self.phone_number[1:].isdigit()
            return True
        except Exception:
            return False


    @staticmethod
    def get(username: str) -> "User":
        """
        Récupère l'utilisateur correspondant au username fourni.
        Paramètres
        ----------
        username : str

        Retourne
        --------
        User

        Lève
        ----
        User.DoesNotExist
            Si aucun utilisateur n'est trouvé.
        PermissionError
            Si le compte est supprimé (status == DELETED),
            uniquement quand le champ status est activé.
        """
        username_field = forge_auth_config.username_field
        try:
            user = User.objects.get(**{username_field: username})
        except User.DoesNotExist:
            raise User.DoesNotExist(f"Utilisateur introuvable : {username}")
        
        if 'status' not in forge_auth_config.optional_fields:
            if user.status == User.StatusVerified.DELETED:
                raise PermissionError("Ce compte a été supprimé.")
        return user

    def __str__(self) -> str:
        username_field = forge_auth_config.username_field
        return getattr(self, username_field)

    class Meta:
        verbose_name          = _("utilisateur")
        verbose_name_plural   = _("utilisateurs")
        ordering              = ("-date_joined",)
        unique_together       = ("phone_number", "email")

# OtpToken n'est créé que si USE_OTP=True ET otp_secret est activé.
_use_otp = forge_auth_config.otp_conf.USE_OTP
_otp_enabled =  "otp_secret" not in forge_auth_config.optional_fields

if _use_otp and _otp_enabled:

    class OtpToken(models.Model):
        """
        Jeton OTP à usage temporaire lié à un utilisateur.

        Ce modèle n'existe que si SCB_AUTH["USE_OTP"] est True (défaut)
        ET que "otp_secret" n'est pas dans SCB_AUTH["OPTIONAL_FIELDS"].
        """

        user = models.OneToOneField(
            User,
            on_delete=models.CASCADE,
            related_name="otp_token",
            verbose_name=_("Utilisateur"),
        )
        token      = models.CharField(max_length=255, verbose_name=_("Token haché"))
        otp_code = models.CharField(max_length=255, null=True, blank=True, verbose_name=_("Code OTP"))
        created_at = models.DateTimeField(auto_now_add=True)
        updated_at = models.DateTimeField(auto_now=True)

        def generate_otp(self, digits: int | None = None) -> str:
            """
            Génère un nouveau code OTP pour l'utilisateur associé.

            Le nombre de chiffres est lu depuis SCB_AUTH["OTP_DIGITS"]
            (défaut : 4) mais peut être surchargé par le paramètre *digits*.

            Paramètres
            ----------
            digits : int, optional
                Nombre de chiffres du code OTP.
            Retourne
            --------
            str
                Le code OTP en clair (à transmettre à l'utilisateur).
            """
            nb_digits = digits or forge_auth_config.otp_conf.OTP_DIGITS
            totp = pyotp.TOTP(self.user.otp_secret, digits=nb_digits)
            code = totp.now()
            self.token = make_password(code)
            self.otp_code = code
            self.save()
            return code
        
        @property
        def get_secure_otp_code(self) -> str:
            otp_code =  self.otp_code
            self.otp_code = None
            self.save(update_fields=["otp_code"])
            return otp_code

        def verify_otp(self, code: str) -> bool:
            """
            Vérifie si le code OTP fourni correspond au token stocké.

            En mode DEBUG, la vérification est toujours True.

            Paramètres
            ----------
            code : str
                Code OTP saisi par l'utilisateur.

            Retourne
            --------
            bool
                True si le code est valide (ou si DEBUG=True).
            """
            return True if getattr(settings, "DEBUG", True) else check_password(code, self.token)

        class Meta:
            verbose_name        = _("jeton OTP")
            verbose_name_plural = _("jetons OTP")

else:
    # Classe fantôme pour permettre les imports sans erreur
    # ("from scb_auth.models import OtpToken" ne plantera pas,
    #  mais instancier OtpToken lèvera NotImplementedError)
    class OtpToken:  # type: ignore[no-redef]
        """
        Placeholder : OtpToken est désactivé dans la configuration actuelle.

        Activez-le en mettant SCB_AUTH["USE_OTP"] = True
        et en retirant "otp_secret" de SCB_AUTH["OPTIONAL_FIELDS"].
        """

        def __init__(self, *args, **kwargs):
            raise NotImplementedError(
                "OtpToken est désactivé. "
                "Vérifiez SCB_AUTH['USE_OTP'] et SCB_AUTH['OPTIONAL_FIELDS']."
            )

