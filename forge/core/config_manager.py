"""
Manipulation programmatique de `settings.py` par analyse syntaxique AST.

Utilise **LibCST** (Concrete Syntax Tree) plutôt qu'une réécriture par regex ou
par template, ce qui garantit :

- La préservation stricte du formatage existant (indentation, commentaires, blank lines).
- La détection de doublons avant toute écriture.
- Des transformations composables et réversibles.

Terminologie LibCST rappelée ici pour le lecteur non initié :
    - `Module`      : racine du fichier entier.
    - `SimpleStatementLine` : une ligne contenant une ou plusieurs instructions simples.
    - `Assign` / `AugAssign` : nœuds d'affectation (`X = ...` / `X += ...`).
    - `List` / `Element` : nœuds représentant les littéraux de liste Python.
    - `ConcatenatedString` / `FormattedString` : f-strings et concaténations.

Public API
----------
    add_to_installed_apps(settings_path, app_label)
    add_to_list_setting(settings_path, setting_name, value)
    add_simple_setting(settings_path, key, value)
    append_raw_block(settings_path, block)
    setting_exists(settings_path, key)
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Sequence, Union

import libcst as cst
import libcst.metadata as meta


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------


def _load(path: Path) -> tuple[str, cst.Module]:
    """Lit le fichier et retourne (source, module CST)."""
    source = path.read_text(encoding="utf-8")
    return source, cst.parse_module(source)


def _save(path: Path, module: cst.Module) -> None:
    """Sérialise le module CST et écrase le fichier."""
    path.write_text(module.code, encoding="utf-8")


def _string_value(node: cst.BaseExpression) -> str | None:
    """
    Extrait la valeur string d'un nœud CST si c'est un littéral simple.
    Retourne None pour tout autre type d'expression.
    """
    if isinstance(node, cst.SimpleString):
        # strip quotes: 'foo' -> foo,  "foo" -> foo
        raw = node.value
        for quote in ('"""', "'''", '"', "'"):
            if raw.startswith(quote) and raw.endswith(quote):
                return raw[len(quote): -len(quote)]
    return None


def _make_string_element(value: str) -> cst.Element:
    """Crée un élément de liste CST pour un littéral string."""
    return cst.Element(
        value=cst.SimpleString(f'"{value}"'),
        comma=cst.MaybeSentinel.DEFAULT,
    )


def _list_string_values(lst: cst.List) -> list[str]:
    """Retourne toutes les valeurs string connues d'une liste CST."""
    values: list[str] = []
    for el in lst.elements:
        v = _string_value(el.value)
        if v is not None:
            values.append(v)
    return values


# ---------------------------------------------------------------------------
# Transformers LibCST
# ---------------------------------------------------------------------------


class _ListAppender(cst.CSTTransformer):
    """
    Ajoute `new_value` à la fin d'une liste Python assignée à `setting_name`
    dans le fichier, à condition qu'il n'y soit pas déjà présent.

    Gère les deux formes :
        INSTALLED_APPS = [...]          (Assign)
        INSTALLED_APPS += [...]         (AugAssign — moins courant)

    L'attribut `modified` indique si une modification a effectivement eu lieu.
    """

    def __init__(self, setting_name: str, new_value: str) -> None:
        super().__init__()
        self.setting_name = setting_name
        self.new_value = new_value
        self.modified = False

    # -- helpers privés -------------------------------------------------------

    def _append_to_list(self, lst: cst.List) -> cst.List:
        """
        Insère un nouvel élément en fin de liste sur une nouvelle ligne
        indentée (4 espaces), quelle que soit la forme originale de la liste
        (inline ou multiligne).
        """
        existing = _list_string_values(lst)
        if self.new_value in existing:
            return lst

        self.modified = True
        elements = list(lst.elements)

        # Whitespace "avant" le nouvel élément : saut de ligne + 4 espaces
        newline_indent = cst.ParenthesizedWhitespace(
            first_line=cst.SimpleWhitespace(""),
            indent=True,
            last_line=cst.SimpleWhitespace("    "),
        )

        # Forcer une virgule + newline sur le dernier élément existant
        if elements:
            last = elements[-1]
            elements[-1] = last.with_changes(
                comma=cst.Comma(whitespace_after=newline_indent)
            )

        # Nouvel élément sans virgule trailing (sera ajouté au prochain appel)
        new_element = cst.Element(
            value=cst.SimpleString(f'"{self.new_value}"'),
            comma=cst.MaybeSentinel.DEFAULT,
        )
        elements.append(new_element)

        # Forcer le ] sur sa propre ligne
        new_lbracket = lst.lbracket
        new_rbracket = lst.rbracket.with_changes(
            whitespace_before=cst.ParenthesizedWhitespace(
                first_line=cst.SimpleWhitespace(""),
                indent=True,
                last_line=cst.SimpleWhitespace(""),
            )
        )

        return lst.with_changes(
            elements=elements,
            lbracket=new_lbracket,
            rbracket=new_rbracket,
        )

    # -- visiteurs ------------------------------------------------------------

    def leave_Assign(
        self, original_node: cst.Assign, updated_node: cst.Assign
    ) -> cst.BaseStatement:
        """Intercepte les assignations simples : SETTING = [...]"""
        for target in updated_node.targets:
            if (
                isinstance(target.target, cst.Name)
                and target.target.value == self.setting_name
                and isinstance(updated_node.value, cst.List)
            ):
                new_list = self._append_to_list(updated_node.value)
                return updated_node.with_changes(value=new_list)
        return updated_node

    def leave_AugAssign(
        self, original_node: cst.AugAssign, updated_node: cst.AugAssign
    ) -> cst.BaseStatement:
        """Intercepte les assignations augmentées : SETTING += [...]"""
        if (
            isinstance(updated_node.target, cst.Name)
            and updated_node.target.value == self.setting_name
            and isinstance(updated_node.value, cst.List)
        ):
            new_list = self._append_to_list(updated_node.value)
            return updated_node.with_changes(value=new_list)
        return updated_node


class _SimpleSettingInserter(cst.CSTTransformer):
    """
    Détecte si `key` est déjà défini dans le module.
    Si non, ajoute `key = <repr(value)>` à la fin du fichier.

    L'insertion en fin de fichier est opérée via `leave_Module`.
    """

    def __init__(self, key: str, value: object) -> None:
        super().__init__()
        self.key = key
        self.value = value
        self._found = False
        self.modified = False

    def visit_Assign(self, node: cst.Assign) -> None:
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == self.key:
                self._found = True

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        if self._found:
            return updated_node  # déjà défini

        self.modified = True
        raw = repr(self.value)
        new_line = cst.parse_statement(f"{self.key} = {raw}\n")
        return updated_node.with_changes(
            body=(*updated_node.body, new_line)
        )


class _SettingExistenceChecker(cst.CSTVisitor):
    """Visite le module et lève un flag dès que `key` est assigné."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.found = False

    def visit_Assign(self, node: cst.Assign) -> None:
        for target in node.targets:
            if isinstance(target.target, cst.Name) and target.target.value == self.key:
                self.found = True


# ---------------------------------------------------------------------------
# API publique
# ---------------------------------------------------------------------------


def add_to_installed_apps(settings_path: Union[str, Path], app_label: str) -> bool:
    """
    Ajoute `app_label` à `INSTALLED_APPS` dans `settings_path`.

    Retourne `True` si le fichier a été modifié, `False` si l'app était
    déjà présente (opération idempotente).

    Raises
    ------
    FileNotFoundError
        Si `settings_path` n'existe pas.
    libcst.ParserSyntaxError
        Si le fichier n'est pas du Python syntaxiquement valide.

    Example
    -------
    >>> add_to_installed_apps("myproject/settings.py", "myapp")
    True
    """
    return add_to_list_setting(settings_path, "INSTALLED_APPS", app_label)


def add_to_list_setting(
    settings_path: Union[str, Path],
    setting_name: str,
    value: str,
) -> bool:
    """
    Ajoute `value` à la liste nommée `setting_name` dans `settings_path`.

    Idempotent : si la valeur est déjà présente, le fichier n'est pas touché
    et la fonction retourne `False`.

    Parameters
    ----------
    settings_path:
        Chemin absolu ou relatif vers `settings.py`.
    setting_name:
        Nom de la variable de liste, p. ex. `"INSTALLED_APPS"`,
        `"MIDDLEWARE"`, `"TEMPLATES"`.
    value:
        Chaîne à insérer, p. ex. `"django.contrib.admin"`.

    Returns
    -------
    bool
        `True` si une modification a été écrite, `False` sinon.
    """
    path = Path(settings_path)
    source, module = _load(path)

    transformer = _ListAppender(setting_name, value)
    new_module = module.visit(transformer)

    if transformer.modified:
        _save(path, new_module)

    return transformer.modified


def add_simple_setting(
    settings_path: Union[str, Path],
    key: str,
    value: object,
) -> bool:
    """
    Ajoute `key = <repr(value)>` à la fin de `settings_path` si `key`
    n'est pas déjà défini.

    Convient pour les scalaires (str, int, bool, None) et les structures
    simples dont le `repr` Python est un littéral valide.

    Pour des blocs complexes (dict imbriqués, appels de fonction, etc.),
    préférer :func:`append_raw_block`.

    Returns
    -------
    bool
        `True` si le fichier a été modifié.
    """
    path = Path(settings_path)
    _, module = _load(path)

    transformer = _SimpleSettingInserter(key, value)
    new_module = module.visit(transformer)

    if transformer.modified:
        _save(path, new_module)

    return transformer.modified


def append_raw_block(
    settings_path: Union[str, Path],
    block: str,
    *,
    guard_comment: str | None = None,
) -> bool:
    """
    Ajoute un bloc de texte brut à la fin de `settings_path`.
    Idempotent via `guard_comment`.
    """
    path = Path(settings_path)
    source = path.read_text(encoding="utf-8")

    normalized = textwrap.dedent(block).strip()

    if guard_comment and guard_comment in source:
        return False

    separator = "\n\n"
    new_source = source.rstrip() + separator + normalized + "\n"
    path.write_text(new_source, encoding="utf-8")
    return True


def replace_or_append_raw_block(
    settings_path: Union[str, Path],
    block: str,
    *,
    guard_comment: str,
) -> bool:
    """
    Remplace le bloc existant délimité par `guard_comment` si présent,
    sinon l'ajoute en fin de fichier.

    Utilisation typique : `DATABASES` qui doit être réécrit à chaque appel
    de `forge configure pgsql` sans créer de doublon.

    Le bloc existant est délimité par `guard_comment` jusqu'au prochain
    bloc vide (double newline) ou fin de fichier.

    Returns
    -------
    bool
        Toujours `True` (le fichier est toujours modifié).
    """
    path = Path(settings_path)
    source = path.read_text(encoding="utf-8")
    normalized = textwrap.dedent(block).strip()

    if guard_comment not in source:
        new_source = source.rstrip() + "\n\n" + normalized + "\n"
        path.write_text(new_source, encoding="utf-8")
        return True

    # Remplace depuis guard_comment jusqu'au prochain \n\n ou fin de fichier
    start = source.index(guard_comment)
    end = source.find("\n\n", start)
    if end == -1:
        end = len(source)
    else:
        end += 2  # inclure le \n\n

    new_source = source[:start] + normalized + "\n\n" + source[end:]
    path.write_text(new_source.rstrip() + "\n", encoding="utf-8")
    return True


def setting_exists(settings_path: Union[str, Path], key: str) -> bool:
    """
    Retourne `True` si `key` est assigné quelque part dans `settings_path`.

    Exemple d'usage avant d'appeler `add_simple_setting` pour du code
    conditionnel dans un moteur de configuration.
    """
    path = Path(settings_path)
    _, module = _load(path)

    checker = _SettingExistenceChecker(key)
    module.visit(checker)
    return checker.found