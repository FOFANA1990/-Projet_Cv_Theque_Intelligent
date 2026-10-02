"""
services/matching.py
---------------------
Scoring des candidatures en base par rapport à une recherche recruteur
(formulaire "Confier une mission" de WizeSource).

Les données candidat sont lues depuis les tables normalisées (Informations,
Compétences, Formations, Expériences, Langues, Centres d'intérêt) — aucun
JSON brut n'est stocké ni relu depuis la base. Pour chaque candidat, on
reconstruit un profil structuré en mémoire (à partir des relations
SQLAlchemy déjà chargées), qu'on transmet ensuite au LLM local (Ollama) pour
obtenir un score de pertinence 0-100 avec une courte justification.

Important (sécurité des threads) : la conversion Candidate (ORM) -> profil
(dict Python simple) se fait intégralement en amont, de façon synchrone et
séquentielle, AVANT toute dispatch de scoring en parallèle. Les tâches
concurrentes ne touchent ensuite plus jamais la session SQLAlchemy (qui
n'est pas thread-safe), seulement des dicts Python déjà en mémoire.
"""

import asyncio
import json
import logging
import re
import uuid

import ollama

from config import settings
from database import SessionLocal
from models import Candidate, Mission, MissionMatch
from schemas import CandidateMatch, MissionSearchRequest

logger = logging.getLogger(__name__)


class MatchingError(Exception):
    """Levée quand le scoring d'un candidat échoue de façon inattendue."""


_SYSTEM_PROMPT = """Tu es un assistant qui évalue la pertinence d'un profil candidat par rapport à une recherche de mission exprimée par une entreprise cliente.

On te donne :
1. Le profil du candidat (poste visé, compétences, expériences, formations), au format JSON.
2. La recherche du recruteur (expertise recherchée, type d'engagement, description de la mission).

Évalue à quel point ce candidat correspond à cette recherche, en te basant uniquement sur les informations fournies (compétences techniques, domaine d'expertise, niveau d'expérience). N'invente aucune information sur le candidat.

Réponds STRICTEMENT avec un objet JSON de cette forme (aucun texte en dehors du JSON) :

{
  "score": 0,
  "justification": "string"
}

Règles :
- "score" est un entier de 0 à 100 : 0 = aucun rapport avec la recherche, 100 = correspondance quasi parfaite.
- "justification" est une phrase courte (une quinzaine de mots maximum) expliquant le score, en français.
- Sois rigoureux : un candidat dont les compétences ne recoupent pas du tout l'expertise recherchée doit avoir un score bas (proche de 0), pas un score moyen par défaut.
"""


# ---------------------------------------------------------------------------
# Conversion ORM -> profil (dict Python simple), lue depuis les tables
# normalisées. Fait une seule fois par candidat, en amont du scoring.
# ---------------------------------------------------------------------------

def _candidate_to_profile(candidate: Candidate) -> dict:
    """
    Reconstruit un profil structuré (même forme que l'ancien JSON du LLM)
    à partir des tables normalisées liées au candidat. Suppose que les
    relations (competences, experiences, formations, langues,
    centres_interet) sont déjà chargées (eager loading côté requête).
    """
    return {
        "id": candidate.id,
        "nom": candidate.nom,
        "prenom": candidate.prenom,
        "email": candidate.email,
        "telephone": candidate.telephone,
        "poste_vise": candidate.poste_vise,
        "competences": [c.competence for c in candidate.competences],
        "experiences": [
            {
                "poste": e.poste,
                "entreprise": e.entreprise,
                "periode": e.periode,
                "description": e.description,
            }
            for e in candidate.experiences
        ],
        "formations": [
            {
                "diplome": f.diplome,
                "etablissement": f.etablissement,
                "annee": f.annee,
            }
            for f in candidate.formations
        ],
        "langues": [l.langue for l in candidate.langues],
        "centres_interet": [c.centre_interet for c in candidate.centres_interet],
    }


_WORD_PATTERN = re.compile(r"[a-zàâäéèêëïîôöùûüç0-9+#\.]{3,}", re.IGNORECASE)


def _extract_keywords(text: str) -> set[str]:
    """Extrait un ensemble de mots significatifs (3 caractères ou plus) d'un texte."""
    return {w.lower() for w in _WORD_PATTERN.findall(text or "")}


def _profile_searchable_text(profile: dict) -> str:
    """Concatène les champs du profil pertinents pour le préfiltrage rapide."""
    parts = [
        profile.get("poste_vise") or "",
        " ".join(profile.get("competences", [])),
    ]
    for exp in profile.get("experiences", []):
        parts.append(exp.get("poste", ""))
        parts.append(exp.get("description", ""))
    return " ".join(parts)


def prefilter_profiles(
    profiles: list[dict], criteria: MissionSearchRequest, top_n: int
) -> list[dict]:
    """
    Filtre rapide (sans appel LLM) qui ne garde que les `top_n` profils les
    plus susceptibles de correspondre à la recherche, par recoupement de
    mots-clés entre le profil candidat et la recherche recruteur.

    But : plafonner le nombre d'appels au LLM (donc le temps total de la
    recherche) indépendamment de la taille de la base de données. C'est une
    heuristique volontairement simple (pas de recherche sémantique) : un
    candidat pertinent mais formulé avec des synonymes différents peut être
    écarté à ce stade. Si la base grandit significativement, remplacer par
    une recherche plein-texte PostgreSQL ou des embeddings serait plus robuste.
    """
    if len(profiles) <= top_n:
        return profiles

    recherche_keywords = _extract_keywords(criteria.expertise) | _extract_keywords(criteria.message)

    scored: list[tuple[int, dict]] = []
    for profile in profiles:
        profile_keywords = _extract_keywords(_profile_searchable_text(profile))
        overlap = len(recherche_keywords & profile_keywords)
        scored.append((overlap, profile))

    # Tri par recoupement décroissant ; en cas d'égalité, on garde l'ordre
    # d'origine (stable) plutôt qu'un ordre arbitraire.
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [profile for _, profile in scored[:top_n]]


def _build_user_prompt(profile: dict, criteria: MissionSearchRequest) -> str:
    profil_json = json.dumps(
        {k: v for k, v in profile.items() if k != "id"},
        ensure_ascii=False,
        indent=2,
    )
    return (
        f"### Profil candidat :\n{profil_json}\n\n"
        f"### Recherche du recruteur :\n"
        f"Expertise recherchée : {criteria.expertise}\n"
        f"Type d'engagement : {criteria.engagement}\n"
        f"Démarrage souhaité : {criteria.demarrage}\n"
        f"Description de la mission : {criteria.message}\n"
    )


def _score_profile_sync(profile: dict, criteria: MissionSearchRequest) -> tuple[int, str]:
    """Appel synchrone à Ollama pour scorer un profil (exécuté dans un thread)."""
    client = ollama.Client(host=settings.OLLAMA_HOST)

    try:
        response = client.chat(
            model=settings.OLLAMA_SCORING_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(profile, criteria)},
            ],
            format="json",
            options={"temperature": 0},
        )
    except Exception as exc:
        raise MatchingError(f"Échec de l'appel au modèle Ollama pour le scoring : {exc}") from exc

    content = response.get("message", {}).get("content", "")

    try:
        parsed = json.loads(content)
        score = int(parsed.get("score", 0))
        justification = str(parsed.get("justification", ""))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.warning(
            "Réponse de scoring inexploitable pour le candidat %s : %s", profile["id"], exc
        )
        score, justification = 0, "Score indisponible (réponse du modèle inexploitable)."

    # Sécurise les bornes [0, 100] même si le modèle dérape légèrement.
    score = max(0, min(100, score))

    return score, justification


async def score_profile(profile: dict, criteria: MissionSearchRequest) -> CandidateMatch:
    """Scorer un profil par rapport aux critères recruteur (sans bloquer l'event loop)."""
    score, justification = await asyncio.to_thread(_score_profile_sync, profile, criteria)

    return CandidateMatch(
        id=profile["id"],
        nom=profile["nom"],
        prenom=profile["prenom"],
        email=profile["email"],
        telephone=profile["telephone"],
        poste_vise=profile["poste_vise"],
        competences=profile["competences"],
        score=score,
        justification=justification,
    )


async def match_candidates(
    candidates: list[Candidate], criteria: MissionSearchRequest
) -> list[CandidateMatch]:
    """
    Score les candidats les plus pertinents par rapport aux critères
    recruteur et retourne les résultats triés par score décroissant.

    Suppose que les relations des candidats (competences, experiences,
    formations, langues, centres_interet) sont déjà chargées (eager
    loading) — voir la requête dans main.py.

    Trois mécanismes bornent le temps total de la recherche :
    1. Conversion ORM -> profils faite une seule fois, en amont, hors thread.
    2. Préfiltrage rapide par mots-clés (`prefilter_profiles`) : ne garde
       que les `settings.MAX_CANDIDATES_TO_SCORE` profils les plus
       prometteurs avant d'appeler le LLM.
    3. Scoring LLM en parallèle, borné par `settings.MATCHING_CONCURRENCY`.
    """
    # Conversion ORM -> dicts, faite ici, séquentiellement, avant toute
    # dispatch concurrente (voir note de sécurité des threads en en-tête).
    profiles = [_candidate_to_profile(c) for c in candidates]

    profils_a_scorer = prefilter_profiles(profiles, criteria, settings.MAX_CANDIDATES_TO_SCORE)

    semaphore = asyncio.Semaphore(settings.MATCHING_CONCURRENCY)

    async def _score_with_limit(profile: dict) -> CandidateMatch | None:
        async with semaphore:
            try:
                return await score_profile(profile, criteria)
            except MatchingError as exc:
                logger.error("Scoring échoué pour le candidat %s : %s", profile["id"], exc)
                return None

    scored = await asyncio.gather(*(_score_with_limit(p) for p in profils_a_scorer))
    results = [match for match in scored if match is not None]

    results.sort(key=lambda m: m.score, reverse=True)
    return results


async def process_mission_matching(mission_id: uuid.UUID) -> None:
    """
    Tâche de fond complète pour une mission recruteur : charge la mission et
    les candidats en base (nouvelle session dédiée, indépendante de celle de
    la requête HTTP déjà terminée), lance le scoring, sauvegarde les
    résultats (table `mission_matches`) et met à jour le statut de la
    mission ("terminee" ou "echec").

    Les résultats ne sont JAMAIS renvoyés au recruteur — uniquement
    consultables par les administrateurs via les endpoints /api/v1/admin/...
    """
    from sqlalchemy.orm import selectinload  # import local pour éviter tout cycle

    db = SessionLocal()
    try:
        mission = db.query(Mission).filter(Mission.id == mission_id).first()
        if mission is None:
            logger.error("Mission %s introuvable pour le matching en tâche de fond.", mission_id)
            return

        candidates = (
            db.query(Candidate)
            .options(
                selectinload(Candidate.competences),
                selectinload(Candidate.experiences),
                selectinload(Candidate.formations),
                selectinload(Candidate.langues),
                selectinload(Candidate.centres_interet),
            )
            .all()
        )

        criteria = MissionSearchRequest(
            societe=mission.societe,
            nom=mission.nom_recruteur or "",
            email=mission.email_recruteur,
            engagement=mission.engagement or "",
            expertise=mission.expertise,
            demarrage=mission.demarrage or "",
            message=mission.description_texte or mission.expertise,
        )

        resultats = await match_candidates(candidates, criteria)

        for r in resultats:
            db.add(
                MissionMatch(
                    mission_id=mission.id,
                    candidate_id=r.id,
                    score=r.score,
                    justification=r.justification,
                )
            )

        mission.statut = "terminee"
        db.commit()
        logger.info(
            "Matching terminé pour la mission %s : %d résultat(s).", mission_id, len(resultats)
        )
    except Exception:
        db.rollback()
        logger.exception("Échec du matching en tâche de fond pour la mission %s", mission_id)
        try:
            mission = db.query(Mission).filter(Mission.id == mission_id).first()
            if mission is not None:
                mission.statut = "echec"
                db.commit()
        except Exception:
            db.rollback()
            logger.exception(
                "Échec supplémentaire lors de la mise à jour du statut d'échec pour %s", mission_id
            )
    finally:
        db.close()