"""
services/france_travail.py
----------------------------
Intégration à l'API officielle "Offres d'emploi v2" de France Travail
(francetravail.io), utilisée pour rechercher des missions/offres externes
correspondant à l'expertise recherchée par un recruteur.

Volontairement basé sur l'API officielle plutôt que sur du scraping du site
France Travail : plus robuste (contrat d'interface stable, JSON structuré),
gratuit, et conforme aux conditions d'utilisation du site.

Authentification : OAuth2 "client credentials" (machine-to-machine). Le
jeton est mis en cache en mémoire pour la durée de sa validité, pour éviter
une authentification à chaque recherche.

Prérequis (à faire une seule fois, manuellement) :
    1. Créer un compte sur https://francetravail.io
    2. Créer une application, souscrire à l'API "Offres d'emploi v2"
    3. Renseigner FRANCE_TRAVAIL_CLIENT_ID / FRANCE_TRAVAIL_CLIENT_SECRET
       dans .env
"""

import logging
import time

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
_SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"
_SCOPE = "api_offresdemploiv2 o2dsoffre"

# Cache mémoire du jeton OAuth2 (process-local ; suffisant pour un usage
# mono-instance comme ce projet). Ré-authentifie automatiquement une fois
# le jeton expiré.
_token_cache: dict = {"access_token": None, "expires_at": 0.0}


class FranceTravailError(Exception):
    """Levée quand l'appel à l'API France Travail échoue (auth, réseau, etc.)."""


class FranceTravailNotConfiguredError(FranceTravailError):
    """Levée quand les identifiants France Travail ne sont pas renseignés en .env."""


def _get_access_token() -> str:
    """Retourne un jeton OAuth2 valide, en le réutilisant depuis le cache si possible."""
    if not settings.FRANCE_TRAVAIL_CLIENT_ID or not settings.FRANCE_TRAVAIL_CLIENT_SECRET:
        raise FranceTravailNotConfiguredError(
            "FRANCE_TRAVAIL_CLIENT_ID / FRANCE_TRAVAIL_CLIENT_SECRET non configurés dans .env "
            "(inscription gratuite requise sur https://francetravail.io)."
        )

    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    try:
        response = httpx.post(
            _TOKEN_URL,
            data={
                "grant_type": "client_credentials",
                "client_id": settings.FRANCE_TRAVAIL_CLIENT_ID,
                "client_secret": settings.FRANCE_TRAVAIL_CLIENT_SECRET,
                "scope": _SCOPE,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise FranceTravailError(
            f"Échec de l'authentification France Travail (HTTP {exc.response.status_code}) : "
            f"vérifiez FRANCE_TRAVAIL_CLIENT_ID/SECRET et que l'application est bien souscrite "
            f"à l'API 'Offres d'emploi v2' sur francetravail.io."
        ) from exc
    except httpx.RequestError as exc:
        raise FranceTravailError(f"Échec réseau lors de l'authentification France Travail : {exc}") from exc

    data = response.json()
    access_token = data.get("access_token")
    expires_in = data.get("expires_in", 1500)  # secondes ; ~25 min par défaut chez FT
    if not access_token:
        raise FranceTravailError("Réponse d'authentification France Travail sans access_token.")

    # Marge de sécurité de 60s avant l'expiration réelle, pour éviter d'utiliser
    # un jeton tout juste expiré en cas de léger décalage d'horloge/latence.
    _token_cache["access_token"] = access_token
    _token_cache["expires_at"] = now + max(expires_in - 60, 30)

    return access_token


def search_offres(mots_cles: str, max_resultats: int = 10) -> list[dict]:
    """
    Recherche des offres d'emploi France Travail correspondant aux mots-clés
    fournis (ex: l'expertise recherchée d'une mission).

    Args:
        mots_cles: termes de recherche (ex: "data analyst python").
        max_resultats: nombre maximum d'offres à retourner (10 par défaut).

    Returns:
        Liste de dicts avec les champs utiles (intitule, entreprise, lieu,
        type_contrat, date_creation, url, description_courte). Liste vide
        si aucune offre ne correspond (HTTP 204 chez France Travail =
        "aucun résultat", pas une erreur).

    Raises:
        FranceTravailNotConfiguredError: identifiants absents de .env.
        FranceTravailError: échec d'authentification ou de la recherche.
    """
    token = _get_access_token()

    try:
        response = httpx.get(
            _SEARCH_URL,
            headers={"Authorization": f"Bearer {token}"},
            params={
                "motsCles": mots_cles,
                "range": f"0-{max(max_resultats - 1, 0)}",
            },
            timeout=15.0,
        )
    except httpx.RequestError as exc:
        raise FranceTravailError(f"Échec réseau lors de la recherche France Travail : {exc}") from exc

    # 204 = requête valide mais aucune offre trouvée (comportement documenté
    # de cette API, à ne pas traiter comme une erreur).
    if response.status_code == 204:
        return []

    if response.status_code not in (200, 206):  # 206 = résultats partiels (pagination)
        raise FranceTravailError(
            f"Échec de la recherche France Travail (HTTP {response.status_code}) : {response.text[:300]}"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise FranceTravailError("Réponse de recherche France Travail non-JSON inattendue.") from exc

    offres_brutes = data.get("resultats", [])
    resultats = []
    for offre in offres_brutes[:max_resultats]:
        lieu = offre.get("lieuTravail", {}) or {}
        entreprise = offre.get("entreprise", {}) or {}
        description = offre.get("description", "") or ""
        resultats.append({
            "intitule": offre.get("intitule", ""),
            "entreprise": entreprise.get("nom") or "Entreprise non précisée",
            "lieu": lieu.get("libelle", ""),
            "type_contrat": offre.get("typeContratLibelle") or offre.get("typeContrat", ""),
            "date_creation": offre.get("dateCreation", ""),
            "url": (offre.get("origineOffre") or {}).get("urlOrigine", ""),
            "description_courte": (description[:280] + "…") if len(description) > 280 else description,
        })

    return resultats
