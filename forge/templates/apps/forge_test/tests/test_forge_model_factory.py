"""
Tests unitaires pour ForgeModelFactory.

Stratégie : on n'a pas besoin d'un vrai projet Django en base.
- Les tests sur la génération de valeurs (fake_for_field, generate_fields_dict)
  utilisent des modèles Django déclarés en mémoire avec `app_label` forcé.
- Les tests sur create/build/save mockent `model.objects.create` et `instance.save`
  pour éviter la base de données.
"""
from __future__ import annotations
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.core.validators import FileExtensionValidator

import uuid
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import json
import django
from django.conf import settings

# Bootstrap Django minimal
if not settings.configured:
    settings.configure(
        DATABASES={"default": {
            "ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.AutoField",
    )
    django.setup()

import unittest

from django.db import models

from forge_test.public.helpers import ForgeModelFactory

# Fake models


class FakeAppConfig:
    label = "testapp"
    name = "testapp"
    module = None
    models_module = None
    default_auto_field = None
    verbose_name = "testapp"


class SimpleModel(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    age = models.IntegerField(default=0)

    class Meta:
        app_label = "testapp"


class AllTypesModel(models.Model):
    char_f = models.CharField(max_length=50)
    text_f = models.TextField()
    email_f = models.EmailField()
    url_f = models.URLField()
    int_f = models.IntegerField()
    pos_int_f = models.PositiveIntegerField()
    big_int_f = models.BigIntegerField()
    float_f = models.FloatField()
    decimal_f = models.DecimalField(max_digits=8, decimal_places=2)
    bool_f = models.BooleanField()
    date_f = models.DateField()
    datetime_f = models.DateTimeField()
    time_f = models.TimeField()
    uuid_f = models.UUIDField()
    json_f = models.JSONField()
    slug_f = models.SlugField()

    class Meta:
        app_label = "testapp"


class UniqueFieldsModel(models.Model):
    code = models.CharField(max_length=50, unique=True)
    ref_email = models.EmailField(unique=True)
    ref_int = models.IntegerField(unique=True)
    ref_slug = models.SlugField(unique=True)
    ref_url = models.URLField(unique=True)

    class Meta:
        app_label = "testapp"


class CustomNameModel(models.Model):
    full_name = models.CharField(max_length=100)
    shop_name = models.CharField(max_length=100)
    bio = models.TextField()

    class Meta:
        app_label = "testapp"


# Helpers
def _field(model: type, name: str) -> models.Field:
    return model._meta.get_field(name)


def _factory(**kwargs: Any) -> ForgeModelFactory:
    return ForgeModelFactory(**kwargs)


# Tests


class TestFakeForField(unittest.TestCase):
    """_fake_for_field retourne le bon type selon le champ."""

    def setUp(self) -> None:
        self.f = _factory()

    def _gen(self, model: type, field_name: str) -> Any:
        return self.f._fake_for_field(_field(model, field_name), model)

    def test_char_field(self) -> None:
        v = self._gen(AllTypesModel, "char_f")
        self.assertIsInstance(v, str)
        self.assertLessEqual(len(v), 50)

    def test_text_field(self) -> None:
        self.assertIsInstance(self._gen(AllTypesModel, "text_f"), str)

    def test_email_field(self) -> None:
        v = self._gen(AllTypesModel, "email_f")
        self.assertIn("@", v)

    def test_url_field(self) -> None:
        v = self._gen(AllTypesModel, "url_f")
        self.assertTrue(v.startswith("http"))

    def test_int_field(self) -> None:
        self.assertIsInstance(self._gen(AllTypesModel, "int_f"), int)

    def test_float_field(self) -> None:
        self.assertIsInstance(self._gen(AllTypesModel, "float_f"), float)

    def test_decimal_field(self) -> None:
        self.assertIsInstance(self._gen(AllTypesModel, "decimal_f"), Decimal)

    def test_bool_field(self) -> None:
        self.assertIsInstance(self._gen(AllTypesModel, "bool_f"), bool)

    def test_uuid_field(self) -> None:
        v = self._gen(AllTypesModel, "uuid_f")
        # faker retourne une str uuid4
        self.assertIsInstance(v, str)
        uuid.UUID(v)  # ne doit pas lever

    def test_json_field(self) -> None:
        v = self._gen(AllTypesModel, "json_f")
        self.assertIsInstance(v, dict)

    def test_slug_field(self) -> None:
        v = self._gen(AllTypesModel, "slug_f")
        self.assertIsInstance(v, str)


class TestUniqueValues(unittest.TestCase):
    """Les champs unique= génèrent des valeurs différentes à chaque appel."""

    def setUp(self) -> None:
        self.f = _factory()

    def _gen(self, field_name: str) -> Any:
        return self.f._fake_for_field(_field(UniqueFieldsModel, field_name), UniqueFieldsModel)

    def test_unique_char_distinct(self) -> None:
        vals = {self._gen("code") for _ in range(20)}
        self.assertGreater(len(vals), 1)

    def test_unique_email_distinct(self) -> None:
        vals = {self._gen("ref_email") for _ in range(20)}
        self.assertGreater(len(vals), 1)

    def test_unique_int_distinct(self) -> None:
        vals = {self._gen("ref_int") for _ in range(20)}
        self.assertGreater(len(vals), 1)

    def test_unique_slug_distinct(self) -> None:
        vals = {self._gen("ref_slug") for _ in range(20)}
        self.assertGreater(len(vals), 1)

    def test_unique_url_distinct(self) -> None:
        vals = {self._gen("ref_url") for _ in range(20)}
        self.assertGreater(len(vals), 1)


class TestCustomNameFields(unittest.TestCase):
    """Les listes person/establishment/text_description sont bien interceptées."""

    def setUp(self) -> None:
        self.f = _factory(
            person_name_fields=["full_name"],
            establishment_name_fields=["shop_name"],
            text_description_fields=["bio"],
            text_word_range=(5, 10),
        )

    def _gen(self, name: str) -> Any:
        return self.f._fake_for_field(_field(CustomNameModel, name), CustomNameModel)

    def test_person_name_is_string(self) -> None:
        v = self._gen("full_name")
        self.assertIsInstance(v, str)
        self.assertLessEqual(len(v), 100)

    def test_establishment_name_is_string(self) -> None:
        v = self._gen("shop_name")
        self.assertIsInstance(v, str)

    def test_text_description_is_string(self) -> None:
        v = self._gen("bio")
        self.assertIsInstance(v, str)


class TestGenerateFieldsDict(unittest.TestCase):
    """generate_fields_dict retourne un dict cohérent."""

    def setUp(self) -> None:
        self.f = _factory()

    def test_returns_dict(self) -> None:
        result = self.f.generate_fields_dict(AllTypesModel)
        self.assertIsInstance(result, dict)

    def test_filtered_fields(self) -> None:
        result = self.f.generate_fields_dict(
            AllTypesModel, fields=["char_f", "int_f"])
        self.assertEqual(set(result.keys()), {"char_f", "int_f"})

    def test_override_applied(self) -> None:
        result = self.f.generate_fields_dict(AllTypesModel, char_f="forced")
        self.assertEqual(result["char_f"], "forced")

    def test_override_with_filter(self) -> None:
        result = self.f.generate_fields_dict(
            AllTypesModel, fields=["char_f"], char_f="forced", int_f=999
        )
        # int_f hors du filtre → absent
        self.assertNotIn("int_f", result)
        self.assertEqual(result["char_f"], "forced")

    def test_no_pk_in_result(self) -> None:
        result = self.f.generate_fields_dict(SimpleModel)
        self.assertNotIn("id", result)


class TestBuild(unittest.TestCase):
    """build() retourne une instance non sauvegardée du bon type."""

    def test_build_returns_instance(self) -> None:
        f = _factory()
        instance = f.build(SimpleModel)
        self.assertIsInstance(instance, SimpleModel)
        # non sauvegardé : pas de pk
        self.assertIsNone(instance.pk)

    def test_build_override(self) -> None:
        f = _factory()
        instance = f.build(SimpleModel, name="Jean")
        self.assertEqual(instance.name, "Jean")


class TestCreate(unittest.TestCase):
    """create() appelle model.objects.create et retourne le bon type."""

    def test_create_calls_db_and_returns_typed(self) -> None:
        f = _factory()
        mock_instance = MagicMock(spec=SimpleModel)
        mock_instance.__class__ = SimpleModel

        with patch.object(SimpleModel.objects, "create", return_value=mock_instance) as mock_create:
            result = f.create(SimpleModel, name="Alice")

        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["name"], "Alice")
        self.assertIs(result, mock_instance)

    def test_create_retries_on_integrity_error(self) -> None:
        f = _factory(max_retries=3)
        mock_instance = MagicMock(spec=SimpleModel)
        mock_instance.__class__ = SimpleModel

        call_count = 0

        def side_effect(**kwargs: Any) -> SimpleModel:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise django.db.IntegrityError("duplicate")
            return mock_instance

        with patch.object(SimpleModel.objects, "create", side_effect=side_effect):
            result = f.create(SimpleModel)

        self.assertEqual(call_count, 3)
        self.assertIs(result, mock_instance)

    def test_create_raises_after_max_retries(self) -> None:
        f = _factory(max_retries=2)

        with patch.object(
            SimpleModel.objects, "create", side_effect=django.db.IntegrityError("dup")
        ):
            with self.assertRaises(RuntimeError):
                f.create(SimpleModel)


class TestSave(unittest.TestCase):
    """save() sauvegarde l'instance issue de build()."""

    def test_save_without_build_raises(self) -> None:
        f = _factory()
        with self.assertRaises(RuntimeError):
            f.save()

    def test_save_calls_instance_save(self) -> None:
        f = _factory()
        instance = f.build(SimpleModel)

        with patch.object(instance, "save") as mock_save:
            f.save()
            mock_save.assert_called_once()

    def test_save_retries_on_integrity_error(self) -> None:
        f = _factory(max_retries=3)
        instance = f.build(SimpleModel)

        call_count = 0

        def side_effect() -> None:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise django.db.IntegrityError("dup")

        with patch.object(instance, "save", side_effect=side_effect):
            f.save()

        self.assertEqual(call_count, 2)


class TestBuildCreateKwargs(unittest.TestCase):
    """build_create_kwargs respecte les exclusions de champs."""

    def test_no_auto_pk(self) -> None:
        f = _factory()
        kwargs = f.build_create_kwargs(SimpleModel)
        self.assertNotIn("id", kwargs)

    def test_default_field_excluded(self) -> None:
        """Les champs avec default ne sont pas inclus si la valeur par défaut est retournée."""
        f = _factory()
        kwargs = f.build_create_kwargs(SimpleModel)
        # age a default=0, donc retourné
        self.assertIn("age", kwargs)
        self.assertEqual(kwargs["age"], 0)

    def test_max_depth_raises(self) -> None:
        f = _factory(max_depth=0)
        with self.assertRaises(RuntimeError, msg="Profondeur maximale"):
            f.build_create_kwargs(SimpleModel, depth=1)


#  FileField / ImageField models


class ImageModel(models.Model):
    photo = models.ImageField(upload_to="photos/")
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)

    class Meta:
        app_label = "testapp"


class FileModelNoValidator(models.Model):
    document = models.FileField(upload_to="docs/")

    class Meta:
        app_label = "testapp"


class FileModelCSV(models.Model):
    report = models.FileField(
        upload_to="reports/",
        validators=[FileExtensionValidator(
            allowed_extensions=["csv", "xlsx"])],
    )

    class Meta:
        app_label = "testapp"


class FileModelPDF(models.Model):
    contract = models.FileField(
        upload_to="contracts/",
        validators=[FileExtensionValidator(allowed_extensions=["pdf"])],
    )

    class Meta:
        app_label = "testapp"


class FileModelImageOnly(models.Model):
    banner = models.FileField(
        upload_to="banners/",
        validators=[FileExtensionValidator(
            allowed_extensions=["png", "jpg", "webp"])],
    )

    class Meta:
        app_label = "testapp"


class FileModelJSON(models.Model):
    payload = models.FileField(
        upload_to="payloads/",
        validators=[FileExtensionValidator(allowed_extensions=["json"])],
    )

    class Meta:
        app_label = "testapp"


#  Tests ImageField


class TestImageField(unittest.TestCase):

    def test_fill_images_false_optional_returns_none(self) -> None:
        """ImageField optionnel (null=True) + fill_images=False → None via _fake_for_field."""
        f = _factory(fill_images=False)
        # avatar est null=True donc _fake_for_field est atteint (pas intercepté avant)
        result = f._fake_for_field(_field(ImageModel, "avatar"), ImageModel)
        self.assertIsNone(result)

    def test_fill_images_false_required_generates_image(self) -> None:
        """ImageField required (null=False) + fill_images=False → image quand même."""
        f = _factory(fill_images=False)
        # photo est required → _build_field_value le génère avant fill_images
        result = f._build_field_value(
            _field(ImageModel, "photo"), 0, ImageModel)
        self.assertIsInstance(result, InMemoryUploadedFile)
        self.assertTrue(result.content_type.startswith("image/"))

    def test_fill_images_true_returns_uploaded_file(self) -> None:
        f = _factory(fill_images=True, image_dimensions=(100, 100))
        result = f._fake_for_field(_field(ImageModel, "photo"), ImageModel)
        self.assertIsInstance(result, InMemoryUploadedFile)

    def test_image_content_type_is_image(self) -> None:
        f = _factory(fill_images=True, image_dimensions=(50, 50))
        result = f._fake_for_field(_field(ImageModel, "photo"), ImageModel)
        self.assertTrue(result.content_type.startswith("image/"))

    def test_image_has_name_with_extension(self) -> None:
        f = _factory(fill_images=True)
        result = f._fake_for_field(_field(ImageModel, "photo"), ImageModel)
        ext = result.name.rsplit(".", 1)[-1].lower()
        self.assertIn(ext, {"png", "jpg", "jpeg",
                      "gif", "webp", "bmp", "tiff"})

    def test_image_dimensions_respected(self) -> None:
        from PIL import Image as PilImage
        f = _factory(fill_images=True, image_dimensions=(123, 456))
        result = f._fake_for_field(_field(ImageModel, "photo"), ImageModel)
        result.seek(0)
        img = PilImage.open(result)
        self.assertEqual(img.size, (123, 456))

    def test_nullable_image_with_fill_false_returns_none(self) -> None:
        f = _factory(fill_images=False)
        # avatar est null=True, blank=True → _build_field_value retourne None avant même _fake_for_field
        result = f._build_field_value(
            _field(ImageModel, "avatar"), 0, ImageModel)
        self.assertIsNone(result)

    def test_image_with_validator_respects_ext(self) -> None:
        """ImageField avec FileExtensionValidator doit respecter l'extension autorisée."""

        class ImageWithValidator(models.Model):
            pic = models.ImageField(
                upload_to="pics/",
                validators=[FileExtensionValidator(
                    allowed_extensions=["webp"])],
            )

            class Meta:
                app_label = "testapp"

        f = _factory(fill_images=True)
        result = f._fake_for_field(
            _field(ImageWithValidator, "pic"), ImageWithValidator)
        self.assertTrue(result.name.endswith(".webp"))


#  Tests FileField


class TestFileField(unittest.TestCase):

    def test_no_validator_generates_txt(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelNoValidator, "document"), FileModelNoValidator)
        self.assertIsInstance(result, InMemoryUploadedFile)
        self.assertTrue(result.name.endswith(".txt"))

    def test_csv_validator_generates_csv(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelCSV, "report"), FileModelCSV)
        self.assertIsInstance(result, InMemoryUploadedFile)
        self.assertTrue(result.name.endswith(".csv"))

    def test_pdf_validator_generates_pdf(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelPDF, "contract"), FileModelPDF)
        self.assertIsInstance(result, InMemoryUploadedFile)
        self.assertTrue(result.name.endswith(".pdf"))

    def test_pdf_content_starts_with_magic_bytes(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelPDF, "contract"), FileModelPDF)
        result.seek(0)
        self.assertTrue(result.read(4) == b"%PDF")

    def test_json_validator_generates_valid_json(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelJSON, "payload"), FileModelJSON)
        result.seek(0)
        parsed = json.loads(result.read().decode("utf-8"))
        self.assertIn("id", parsed)
        self.assertIn("name", parsed)

    def test_csv_content_has_header(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelCSV, "report"), FileModelCSV)
        result.seek(0)
        text = result.read().decode("utf-8")
        self.assertIn("id,name,email,value", text)

    def test_image_only_validator_fill_false_returns_none(self) -> None:
        """FileField avec extensions image uniquement + fill_images=False → None."""
        f = _factory(fill_images=False)
        result = f._fake_for_field(
            _field(FileModelImageOnly, "banner"), FileModelImageOnly)
        self.assertIsNone(result)

    def test_image_only_validator_fill_true_returns_file(self) -> None:
        """FileField avec extensions image uniquement + fill_images=True → image."""
        f = _factory(fill_images=True, image_dimensions=(80, 80))
        result = f._fake_for_field(
            _field(FileModelImageOnly, "banner"), FileModelImageOnly)
        self.assertIsInstance(result, InMemoryUploadedFile)
        ext = result.name.rsplit(".", 1)[-1].lower()
        self.assertIn(ext, {"png", "jpg", "jpeg", "webp"})

    def test_file_size_nonzero(self) -> None:
        f = _factory()
        result = f._fake_for_field(
            _field(FileModelPDF, "contract"), FileModelPDF)
        self.assertGreater(result.size, 0)

    def test_required_filefield_generated_even_fill_images_false(self) -> None:
        """FileField required → généré même si fill_images=False."""
        f = _factory(fill_images=False)
        result = f._build_field_value(
            _field(FileModelPDF, "contract"), 0, FileModelPDF)
        self.assertIsInstance(result, InMemoryUploadedFile)

    def test_required_filefield_image_only_fill_false_still_generates(self) -> None:
        """FileField required avec exts image seulement + fill_images=False → image générée."""
        class RequiredBanner(models.Model):
            banner = models.FileField(
                upload_to="banners/",
                validators=[FileExtensionValidator(
                    allowed_extensions=["png", "webp"])],
            )

            class Meta:
                app_label = "testapp"

        f = _factory(fill_images=False)
        result = f._build_field_value(
            _field(RequiredBanner, "banner"), 0, RequiredBanner)
        self.assertIsInstance(result, InMemoryUploadedFile)
        ext = result.name.rsplit(".", 1)[-1].lower()
        self.assertIn(ext, {"png", "webp"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
