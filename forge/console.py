"""
forge/console.py
================
Sortie console robuste et multi-plateforme.

Sous Windows, la console utilise par défaut une page de code héritée
(cp1252 / OEM). Les accents et symboles de la CLI (``→``, ``•``, ``✓``, ``é``)
s'affichent alors en *mojibake* — p. ex. ``méta-framework`` au lieu de
``méta-framework``. Ce module force la sortie en UTF-8 dès le chargement du
package (voir ``forge/__init__.py``).
"""

from __future__ import annotations

import sys


def enable_utf8_output() -> None:
    """Reconfigure ``stdout`` / ``stderr`` en UTF-8 quand c'est possible.

    - No-op sur les flux déjà en UTF-8 (macOS / Linux, la plupart des shells).
    - No-op silencieux sur les flux non reconfigurables (redirigés, détachés,
      capturés par pytest…) : cette fonction ne doit **jamais** faire échouer
      la CLI pour un simple problème d'encodage d'affichage.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue

        current = (getattr(stream, "encoding", None) or "").lower().replace("-", "")
        if current == "utf8":
            continue

        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, LookupError, OSError):
            # Flux non reconfigurable : on laisse l'encodage d'origine.
            pass
