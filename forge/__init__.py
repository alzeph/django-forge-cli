"""
Package Django Forge.

Active la sortie UTF-8 dès l'import du package, afin que les accents et
symboles de la CLI s'affichent correctement dans tous les terminaux — en
particulier sous Windows, dont la console utilise une page de code héritée
par défaut. Voir ``forge/console.py``.
"""

from forge.console import enable_utf8_output

enable_utf8_output()
