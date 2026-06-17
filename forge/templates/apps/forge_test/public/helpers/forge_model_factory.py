from __future__ import annotations

import csv as csv_module
import io
import json
from typing import Any, Dict, List, Optional, Set, Tuple, Type, TypeVar

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import FileExtensionValidator
from django.db import IntegrityError, models
from django.utils import timezone
from faker import Faker

User = get_user_model()

M = TypeVar("M", bound=models.Model)

__all__ = ["ForgeModelFactory"]

# Extensions reconnues et leur générateur
_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp", "tiff"}
_FILE_GENERATORS: Dict[str, str] = {
    "pdf":  "pdf",
    "csv":  "csv",
    "json": "json",
    "txt":  "txt",
    "xml":  "xml",
    "html": "html",
    "png":  "image",
    "jpg":  "image",
    "jpeg": "image",
    "gif":  "image",
    "webp": "image",
    "bmp":  "image",
    "tiff": "image",
}


class ForgeModelFactory:
    """
    Génère des données valides pour créer une instance d'un modèle Django.
    Supporte ForeignKey, OneToOne et ManyToMany.
    Gère les champs uniques, les contraintes d'intégrité, ImageField et FileField.
    """

    def __init__(
        self,
        max_depth: int = 5,
        create_m2m: bool = True,
        m2m_count: int = 2,
        max_retries: int = 3,
        person_name_fields: Optional[List[str]] = None,
        establishment_name_fields: Optional[List[str]] = None,
        text_description_fields: Optional[List[str]] = None,
        text_word_range: Tuple[int, int] = (10, 50),
        fill_images: bool = False,
        image_dimensions: Tuple[int, int] = (800, 600),
    ) -> None:
        self.fake = Faker()
        self.max_depth = max_depth
        self.create_m2m = create_m2m
        self.m2m_count = m2m_count
        self.max_retries = max_retries

        self.person_name_fields: List[str] = person_name_fields or []
        self.establishment_name_fields: List[str] = establishment_name_fields or [
        ]
        self.text_description_fields: List[str] = text_description_fields or []

        self.text_word_range = text_word_range
        self.fill_images = fill_images
        self.image_dimensions = image_dimensions

        self.user: Optional[models.Model] = None
        self.credentials_user: Optional[Dict[str, str]] = None

        self._cache: Dict[Tuple[Type[models.Model], int], models.Model] = {}
        self._m2m_data: Dict[str, List[models.Model]] = {}
        self._instance: Optional[models.Model] = None
        self._unique_values: Dict[str, Set[Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_user(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
        **user_kwargs: Any,
    ) -> models.Model:
        """Crée ou récupère un utilisateur Django."""
        if username is None:
            username = self.fake.user_name() + str(self.fake.random_int(min=1000, max=9999))
        if password is None:
            password = self.fake.password(length=12, special_chars=True)

        self.credentials_user = {"username": username, "password": password}

        default_user_data: Dict[str, Any] = {
            "email": user_kwargs.pop("email", self.fake.email()),
            "first_name": user_kwargs.pop("first_name", self.fake.first_name()),
            "last_name": user_kwargs.pop("last_name", self.fake.last_name()),
        }
        default_user_data.update(user_kwargs)

        for attempt in range(self.max_retries):
            try:
                self.user = User.objects.create_user(
                    username=username,
                    password=password,
                    **default_user_data,
                )
                return self.user
            except IntegrityError:
                username = self.fake.user_name() + str(self.fake.random_int(min=1000, max=9999))
                self.credentials_user["username"] = username
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Impossible de créer un utilisateur unique après {self.max_retries} tentatives"
                    )

        raise RuntimeError("create_user: échec inattendu")

    def create(self, model: Type[M], **override_kwargs: Any) -> M:
        """Crée et sauvegarde une instance du modèle avec les ManyToMany."""
        self._cache = {}
        self._m2m_data = {}
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                kwargs = self.build_create_kwargs(model)
                kwargs.update(override_kwargs)
                instance: M = model.objects.create(**kwargs)

                if self._m2m_data:
                    for field_name, related_objects in self._m2m_data.items():
                        getattr(instance, field_name).set(related_objects)

                self._instance = instance
                return instance

            except IntegrityError as e:
                last_error = e
                self._flush_unique_cache(model)
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Impossible de créer une instance de {model.__name__} "
                        f"après {self.max_retries} tentatives. Erreur: {last_error}"
                    )

        raise RuntimeError("create: échec inattendu")

    def build(self, model: Type[M], **override_kwargs: Any) -> M:
        """Construit une instance du modèle SANS la sauvegarder."""
        self._cache = {}
        self._m2m_data = {}

        kwargs = self.build_create_kwargs(model)
        kwargs.update(override_kwargs)

        instance: M = model(**kwargs)
        self._instance = instance
        return instance

    def save(self) -> models.Model:
        """Sauvegarde l'instance créée avec build()."""
        if self._instance is None:
            raise RuntimeError(
                "Aucune instance à sauvegarder. Utilisez build() ou create() d'abord.")

        last_error: Optional[Exception] = None
        model_class = type(self._instance)

        for attempt in range(self.max_retries):
            try:
                self._instance.save()

                if self._m2m_data:
                    for field_name, related_objects in self._m2m_data.items():
                        getattr(self._instance, field_name).set(
                            related_objects)

                return self._instance

            except IntegrityError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    for field in model_class._meta.fields:
                        if field.unique and not field.primary_key:
                            setattr(self._instance, field.name,
                                    self._fake_for_field(field, model_class))
                else:
                    raise RuntimeError(
                        f"Impossible de sauvegarder l'instance après "
                        f"{self.max_retries} tentatives. Erreur: {last_error}"
                    )

        raise RuntimeError("save: échec inattendu")

    def generate_fields_dict(
        self,
        model: Type[M],
        fields: Optional[List[str]] = None,
        **override_kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Retourne un dict de valeurs fake pour les champs du modèle sans créer d'instance en base.

        Args:
            model: Le modèle Django cible.
            fields: Liste de noms de champs à inclure (None = tous les champs non-PK).
            **override_kwargs: Valeurs forcées qui écrasent le fake.
        """
        self._cache = {}
        self._m2m_data = {}

        all_kwargs = self.build_create_kwargs(model)

        if fields is not None:
            all_kwargs = {k: v for k, v in all_kwargs.items() if k in fields}

        all_kwargs.update(
            {k: v for k, v in override_kwargs.items() if fields is None or k in fields}
        )

        return all_kwargs

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def build_create_kwargs(self, model: Type[models.Model], depth: int = 0) -> Dict[str, Any]:
        if depth > self.max_depth:
            raise RuntimeError(
                f"Profondeur maximale atteinte ({self.max_depth}). Relation circulaire possible."
            )

        data: Dict[str, Any] = {}

        for field in model._meta.fields:
            if field.primary_key and isinstance(field, models.AutoField):
                continue
            if field.name in data:
                continue
            value = self._build_field_value(field, depth, model)
            if value is not None:
                data[field.name] = value

        if self.create_m2m:
            self._prepare_m2m_data(model, depth)

        return data

    def _prepare_m2m_data(self, model: Type[models.Model], depth: int) -> None:
        self._m2m_data = {}
        for field in model._meta.many_to_many:
            if field.remote_field.through._meta.auto_created:
                related_objects: List[models.Model] = [
                    self._build_related(field.related_model, depth)
                    for _ in range(self.m2m_count)
                ]
                self._m2m_data[field.name] = related_objects

    def _build_field_value(
        self,
        field: models.Field,
        depth: int,
        model: Type[models.Model],
    ) -> Any:
        if field.has_default():
            default = field.default
            return default() if callable(default) else default

        if field.null and field.blank:
            return None

        # Champ fichier/image requis (null=False, blank=False) :
        # forcer la génération indépendamment de fill_images
        if isinstance(field, models.ImageField) and not field.null and not field.blank:
            return self._generate_image(field)

        if (
            isinstance(field, models.FileField)
            and not isinstance(field, models.ImageField)
            and not field.null
            and not field.blank
        ):
            return self._generate_file(field)

        if field.choices:
            return field.choices[0][0]

        if isinstance(field, models.ForeignKey):
            if field.related_model == User and self.user is not None:
                return self.user
            return self._build_related(field.related_model, depth)

        if isinstance(field, models.OneToOneField):
            return self._build_related(field.related_model, depth)

        return self._fake_for_field(field, model)

    def _build_related(self, model: Type[M], depth: int) -> M:
        cache_key = (model, depth)
        if cache_key in self._cache:
            return self._cache[cache_key]  # type: ignore[return-value]
        obj: M = self._create_with_retries(model, depth + 1)
        self._cache[cache_key] = obj
        return obj

    def _create_with_retries(self, model: Type[M], depth: int = 0) -> M:
        last_error: Optional[Exception] = None

        for attempt in range(self.max_retries):
            try:
                kwargs = self.build_create_kwargs(model, depth)
                obj: M = model.objects.create(**kwargs)
                return obj
            except IntegrityError as e:
                last_error = e
                self._flush_unique_cache(model)
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Impossible de créer une instance de {model.__name__} "
                        f"après {self.max_retries} tentatives. Erreur: {last_error}"
                    )

        raise RuntimeError("_create_with_retries: échec inattendu")

    # ------------------------------------------------------------------
    # Fake value generation
    # ------------------------------------------------------------------

    def _fake_for_field(self, field: models.Field, model: Type[models.Model]) -> Any:
        unique_key = f"{model.__name__}.{field.name}"

        # --- custom field-name interceptions ---
        if field.name in self.person_name_fields:
            if isinstance(field, (models.CharField, models.TextField)):
                v = self.fake.name()
                return v[: field.max_length] if isinstance(field, models.CharField) and field.max_length else v

        if field.name in self.establishment_name_fields:
            if isinstance(field, (models.CharField, models.TextField)):
                v = self.fake.company()
                return v[: field.max_length] if isinstance(field, models.CharField) and field.max_length else v

        if field.name in self.text_description_fields:
            if isinstance(field, (models.CharField, models.TextField)):
                min_w, max_w = self.text_word_range
                v = self.fake.sentence(
                    nb_words=self.fake.random_int(min=min_w, max=max_w))
                return v[: field.max_length] if isinstance(field, models.CharField) and field.max_length else v

        # --- type dispatch ---
        # Sous-classes de CharField en premier pour éviter le court-circuit

        if isinstance(field, models.EmailField):
            if field.unique:
                return self._unique_email(unique_key)
            return self.fake.email()

        if isinstance(field, models.URLField):
            if field.unique:
                ts = self.fake.random_int(min=100000, max=999999)
                url = f"https://{self.fake.domain_name()}/{ts}"
                self._register_unique(unique_key, url)
                return url
            return self.fake.url()

        if isinstance(field, models.SlugField):
            if field.unique:
                ts = self.fake.random_int(min=100000, max=999999)
                slug = f"{self.fake.slug()}-{ts}"[: field.max_length or 50]
                self._register_unique(unique_key, slug)
                return slug
            return self.fake.slug()

        # ImageField hérite de FileField → tester avant FileField.
        # Ici on arrive uniquement pour les champs optionnels (null=True ou blank=True)
        # car les champs requis sont interceptés dans _build_field_value.
        if isinstance(field, models.ImageField):
            # Optionnel : respecter fill_images
            return self._generate_image(field) if self.fill_images else None

        if isinstance(field, models.FileField):
            # Pour un FileField optionnel dont toutes les exts sont des images,
            # respecter fill_images comme pour ImageField.
            allowed = self._allowed_extensions(field)
            if allowed and all(e in _IMAGE_EXTS for e in allowed) and not self.fill_images:
                return None
            return self._generate_file(field)

        if isinstance(field, models.CharField):
            return self._unique_str(unique_key, field.max_length or 50, field.unique)

        if isinstance(field, models.TextField):
            return self.fake.text(max_nb_chars=400)

        if isinstance(field, (models.IntegerField, models.PositiveIntegerField)):
            if field.unique:
                return self._unique_int(unique_key)
            return self.fake.random_int(min=0, max=1000)

        if isinstance(field, models.BigIntegerField):
            return self.fake.random_int(min=0, max=999999)

        if isinstance(field, models.FloatField):
            return self.fake.pyfloat(min_value=0, max_value=1000)

        if isinstance(field, models.DecimalField):
            max_digits = field.max_digits or 10
            decimal_places = field.decimal_places or 2
            return self.fake.pydecimal(
                left_digits=max_digits - decimal_places,
                right_digits=decimal_places,
                positive=True,
            )

        if isinstance(field, models.BooleanField):
            return self.fake.boolean()

        if isinstance(field, models.DateField):
            return timezone.now().date()

        if isinstance(field, models.DateTimeField):
            return timezone.now()

        if isinstance(field, models.TimeField):
            return timezone.now().time()

        if isinstance(field, models.UUIDField):
            return self.fake.uuid4()

        if isinstance(field, models.JSONField):
            return {"key": "value"}

        return None

    # ------------------------------------------------------------------
    # File / Image generators
    # ------------------------------------------------------------------

    def _allowed_extensions(self, field: models.FileField) -> List[str]:
        """
        Extrait les extensions autorisées depuis les validators FileExtensionValidator
        du champ. Retourne une liste vide si aucun validator ne le précise.
        """
        exts: List[str] = []
        for v in field.validators:
            if isinstance(v, FileExtensionValidator) and v.allowed_extensions:
                exts.extend([e.lower() for e in v.allowed_extensions])
        return exts

    def _pick_extension(self, allowed: List[str], prefer_image: bool = False) -> str:
        """
        Choisit l'extension à générer selon la liste autorisée.
        Si la liste est vide, retourne 'png' pour image, 'txt' pour file.
        """
        if not allowed:
            return "png" if prefer_image else "txt"

        if prefer_image:
            # Préférer une extension image parmi celles autorisées
            img_exts = [e for e in allowed if e in _IMAGE_EXTS]
            return img_exts[0] if img_exts else allowed[0]

        # Pour FileField générique : préférer non-image si possible
        non_img = [e for e in allowed if e not in _IMAGE_EXTS]
        return non_img[0] if non_img else allowed[0]

    def _generate_image(self, field: models.ImageField) -> InMemoryUploadedFile:
        """
        Génère une image en mémoire (Pillow).
        L'extension est déduite des validators ; fallback PNG.
        Les dimensions respectent self.image_dimensions.
        """
        try:
            from PIL import Image as PilImage
        except ImportError:
            raise RuntimeError(
                "Pillow est requis pour générer des images (fill_images=True). "
                "Installez-le via : pip install Pillow"
            )

        allowed = self._allowed_extensions(field)
        ext = self._pick_extension(allowed, prefer_image=True)

        # Mapping ext → format Pillow
        pil_format_map = {
            "jpg": "JPEG",
            "jpeg": "JPEG",
            "png": "PNG",
            "gif": "GIF",
            "webp": "WEBP",
            "bmp": "BMP",
            "tiff": "TIFF",
        }
        pil_format = pil_format_map.get(ext, "PNG")
        mime_type = f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"

        width, height = self.image_dimensions
        img = PilImage.new("RGB", (width, height), color=self.fake.color())

        buffer = io.BytesIO()
        img.save(buffer, format=pil_format)
        buffer.seek(0)

        filename = f"{self.fake.slug()}.{ext}"
        return InMemoryUploadedFile(
            file=buffer,
            field_name=field.name,
            name=filename,
            content_type=mime_type,
            size=buffer.getbuffer().nbytes,
            charset=None,
        )

    def _generate_file(self, field: models.FileField) -> Optional[InMemoryUploadedFile]:
        """
        Génère un fichier en mémoire selon les extensions autorisées par le champ.
        Supporte : txt, csv, json, pdf (minimal), xml, html, et formats image.
        Retourne None si le champ est optionnel et fill_images=False pour les images.
        """
        allowed = self._allowed_extensions(field)
        ext = self._pick_extension(allowed, prefer_image=False)

        # Si l'extension choisie est une image, déléguer au générateur image.
        # On génère inconditionnellement ici : _generate_file est appelé
        # soit depuis _build_field_value (champ requis, toujours générer),
        # soit depuis _fake_for_field (champ optionnel, fill_images déjà vérifié en amont).
        if ext in _IMAGE_EXTS:
            return self._generate_image(field)  # type: ignore[arg-type]

        content, mime_type = self._build_file_content(ext)
        filename = f"{self.fake.slug()}.{ext}"
        buffer = io.BytesIO(content)

        return InMemoryUploadedFile(
            file=buffer,
            field_name=field.name,
            name=filename,
            content_type=mime_type,
            size=len(content),
            charset="utf-8" if mime_type.startswith(
                "text") or mime_type == "application/json" else None,
        )

    def _build_file_content(self, ext: str) -> Tuple[bytes, str]:
        """Retourne (contenu_bytes, mime_type) pour une extension donnée."""

        if ext == "txt":
            content = self.fake.text(max_nb_chars=500).encode("utf-8")
            return content, "text/plain"

        if ext == "csv":
            buf = io.StringIO()
            writer = csv_module.writer(buf)
            writer.writerow(["id", "name", "email", "value"])
            for i in range(5):
                writer.writerow([
                    i + 1,
                    self.fake.name(),
                    self.fake.email(),
                    self.fake.random_int(min=0, max=1000),
                ])
            return buf.getvalue().encode("utf-8"), "text/csv"

        if ext == "json":
            payload = {
                "id": str(self.fake.uuid4()),
                "name": self.fake.name(),
                "email": self.fake.email(),
                "items": [self.fake.word() for _ in range(3)],
            }
            return json.dumps(payload, indent=2).encode("utf-8"), "application/json"

        if ext == "pdf":
            # PDF minimal valide (structure de base sans lib externe)
            content = self._minimal_pdf()
            return content, "application/pdf"

        if ext == "xml":
            content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                f"<root><name>{self.fake.name()}</name>"
                f"<email>{self.fake.email()}</email></root>"
            ).encode("utf-8")
            return content, "application/xml"

        if ext == "html":
            content = (
                "<!DOCTYPE html><html><head>"
                f"<title>{self.fake.sentence(nb_words=3)}</title></head>"
                f"<body><h1>{self.fake.name()}</h1>"
                f"<p>{self.fake.text(max_nb_chars=200)}</p></body></html>"
            ).encode("utf-8")
            return content, "text/html"

        # Fallback : texte brut
        return self.fake.text(max_nb_chars=200).encode("utf-8"), "application/octet-stream"

    def _minimal_pdf(self) -> bytes:
        """
        Génère un PDF syntaxiquement valide sans dépendance externe.
        Contient une page blanche avec un texte simple.
        """
        text_line = self.fake.sentence(
            nb_words=6).replace("(", "").replace(")", "")
        stream_content = f"BT /F1 12 Tf 100 700 Td ({text_line}) Tj ET"
        stream_bytes = stream_content.encode("latin-1")
        stream_len = len(stream_bytes)

        pdf = (
            b"%PDF-1.4\n"
            b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
            b" /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
            + f"4 0 obj\n<< /Length {stream_len} >>\nstream\n".encode()
            + stream_bytes
            + b"\nendstream\nendobj\n"
            b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000360 00000 n \n"
            b"trailer\n<< /Size 6 /Root 1 0 R >>\n"
            b"startxref\n459\n%%EOF\n"
        )
        return pdf

    # ------------------------------------------------------------------
    # Unique value helpers
    # ------------------------------------------------------------------

    def _register_unique(self, key: str, value: Any) -> None:
        self._unique_values.setdefault(key, set()).add(value)

    def _unique_str(self, unique_key: str, max_length: int, is_unique: bool) -> str:
        if not is_unique:
            return self.fake.word()[:max_length]
        base = self.fake.word()[: max(1, max_length - 15)]
        value = base  # satisfait mypy avant la boucle
        for _ in range(10):
            suffix = str(self.fake.random_int(min=100000, max=999999))
            value = f"{base}_{suffix}"[:max_length]
            if value not in self._unique_values.get(unique_key, set()):
                self._register_unique(unique_key, value)
                return value
        self._register_unique(unique_key, value)
        return value

    def _unique_email(self, unique_key: str) -> str:
        email = ""
        for _ in range(10):
            ts = self.fake.random_int(min=100000, max=999999)
            email = f"{self.fake.user_name()}{ts}@{self.fake.domain_name()}"
            if email not in self._unique_values.get(unique_key, set()):
                self._register_unique(unique_key, email)
                return email
        self._register_unique(unique_key, email)
        return email

    def _unique_int(self, unique_key: str) -> int:
        value = 0
        for _ in range(10):
            value = self.fake.random_int(min=100000, max=9999999)
            if value not in self._unique_values.get(unique_key, set()):
                self._register_unique(unique_key, value)
                return value
        self._register_unique(unique_key, value)
        return value

    def _flush_unique_cache(self, model: Type[models.Model]) -> None:
        prefix = f"{model.__name__}."
        for key in [k for k in self._unique_values if k.startswith(prefix)]:
            self._unique_values.pop(key, None)
