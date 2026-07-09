"""
Page d'accueil d'onboarding Django Forge.

Générée par ``forge init`` dans le package du projet. Branchée sur ``/`` par
``urls.py``. Elle remplace la page « fusée » générique de Django par un écran
qui confirme que le projet tourne, invite à soutenir le projet sur GitHub, et
liste les prochaines étapes pour terminer son application.

Comportement :
- ``DEBUG = True``  → affiche la page d'onboarding.
- ``DEBUG = False`` → renvoie 404 : à vous de brancher votre vraie page
  d'accueil sur ``/`` (cette page est un outil de développement, pas une page
  publique).
"""

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound

GITHUB_URL = "https://github.com/alzeph/django-forge-cli"

_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{project_name}} — Django Forge</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: radial-gradient(1200px 600px at 50% -10%, #1b2a4a 0%, #0b1020 55%, #070b16 100%);
    color: #e8ecf5; line-height: 1.55;
  }
  .wrap { max-width: 860px; margin: 0 auto; padding: 56px 22px 80px; }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    font-size: 13px; letter-spacing: .04em; text-transform: uppercase;
    color: #8fb4ff; background: rgba(80,130,255,.10);
    border: 1px solid rgba(120,160,255,.25); border-radius: 999px; padding: 6px 14px;
  }
  h1 { font-size: clamp(30px, 6vw, 46px); margin: 20px 0 6px; letter-spacing: -.02em; }
  h1 .fire { filter: drop-shadow(0 4px 14px rgba(255,140,60,.45)); }
  .sub { font-size: 18px; color: #aab6cf; margin: 0 0 30px; }
  .ok { color: #63e6a8; font-weight: 600; }

  .star {
    display: inline-flex; align-items: center; gap: 10px; text-decoration: none;
    background: linear-gradient(180deg, #ffd24a, #f5a623); color: #2a1a00; font-weight: 700;
    padding: 13px 20px; border-radius: 12px; font-size: 16px;
    box-shadow: 0 8px 26px rgba(245,166,35,.35); transition: transform .12s ease, box-shadow .12s ease;
  }
  .star:hover { transform: translateY(-2px); box-shadow: 0 12px 32px rgba(245,166,35,.5); }
  .star-note { margin: 12px 0 40px; color: #8b97b3; font-size: 14px; }
  .star-note a { color: #9fc0ff; }

  h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .08em; color: #7f8db0; margin: 40px 0 16px; }
  ol.steps { list-style: none; counter-reset: s; margin: 0; padding: 0; display: grid; gap: 12px; }
  ol.steps li {
    counter-increment: s; position: relative;
    background: rgba(255,255,255,.03); border: 1px solid rgba(255,255,255,.07);
    border-radius: 14px; padding: 16px 18px 16px 60px;
  }
  ol.steps li::before {
    content: counter(s); position: absolute; left: 16px; top: 16px;
    width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
    font-weight: 700; color: #cfe0ff; background: rgba(90,140,255,.16); border: 1px solid rgba(120,160,255,.28);
  }
  ol.steps b { color: #fff; }
  ol.steps p { margin: 4px 0 0; color: #aab6cf; font-size: 14.5px; }
  code {
    font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; font-size: 13.5px;
    background: rgba(10,14,26,.85); border: 1px solid rgba(255,255,255,.10);
    padding: 2px 7px; border-radius: 6px; color: #ffd9a0; white-space: nowrap;
  }
  .foot { margin-top: 46px; padding-top: 22px; border-top: 1px solid rgba(255,255,255,.08); color: #6f7c9c; font-size: 13px; }
  .foot a { color: #9fc0ff; }
  .tip { margin-top: 8px; color: #63e6a8; }
</style>
</head>
<body>
<div class="wrap">
  <span class="badge">🔥 Django Forge</span>
  <h1><span class="fire">🔥</span> Projet <em>{{project_name}}</em> opérationnel</h1>
  <p class="sub"><span class="ok">✓ Le serveur tourne.</span> Votre projet Django Forge est prêt à être développé.</p>

  <a class="star" href="{{GITHUB_URL}}" target="_blank" rel="noopener">⭐ Mettez une étoile sur GitHub</a>
  <p class="star-note">
    Django Forge vous fait gagner du temps ? Un simple ⭐ aide énormément le projet.<br>
    Dépôt d'origine : <a href="{{GITHUB_URL}}" target="_blank" rel="noopener">{{GITHUB_URL}}</a>
  </p>

  <h2>Et maintenant ? — pour terminer votre projet</h2>
  <ol class="steps">
    <li><b>Créez un compte administrateur</b>
      <p>Exécutez <code>forge createsuperuser</code>, puis connectez-vous sur <code>/admin/</code>.</p></li>
    <li><b>Créez vos applications métier</b>
      <p><code>forge add blog</code> — l'app est créée, ajoutée à <code>INSTALLED_APPS</code> et ses URLs sont branchées automatiquement.</p></li>
    <li><b>Définissez vos modèles, puis migrez</b>
      <p>Éditez <code>votre_app/models.py</code>, puis <code>forge makemigrations</code> et <code>forge migrate</code>.</p></li>
    <li><b>Ajoutez des services au besoin</b>
      <p><code>forge configure drf</code> (API REST), <code>redis</code>, <code>celery</code>, <code>channels</code>, <code>pgsql</code>…</p></li>
    <li><b>Installez des modules prêts à l'emploi</b>
      <p><code>forge install forge-auth</code> pour l'authentification JWT + OTP (dépendances résolues automatiquement).</p></li>
    <li><b>Remplacez cette page d'accueil</b>
      <p>Branchez votre vraie vue sur <code>/</code> dans <code>{{project_name}}/urls.py</code>. Cette page ne s'affiche qu'en développement (<code>DEBUG=True</code>).</p></li>
  </ol>

  <p class="tip">💡 Astuce : <code>forge</code> relaie toutes les commandes Django natives — <code>forge shell</code>, <code>forge test</code>, <code>forge dbshell</code>… fonctionnent tels quels.</p>

  <div class="foot">
    Généré par <a href="{{GITHUB_URL}}" target="_blank" rel="noopener">Django Forge</a>.
    Astuce dev : cette page vit dans <code>{{project_name}}/welcome.py</code>.
  </div>
</div>
</body>
</html>"""

_PAGE = _PAGE.replace("{{GITHUB_URL}}", GITHUB_URL)


def forge_welcome(request):
    """Page d'onboarding en développement ; 404 en production."""
    if not settings.DEBUG:
        return HttpResponseNotFound(
            "Page d'accueil Django Forge désactivée hors DEBUG. "
            "Branchez votre propre vue sur '/'."
        )
    return HttpResponse(_PAGE)
