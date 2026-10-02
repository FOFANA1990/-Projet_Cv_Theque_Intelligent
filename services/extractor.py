"""
services/extractor.py
----------------------
Pipeline d'extraction structurée des informations d'un CV, en s'appuyant sur
un LLM local servi par Ollama (par défaut : Qwen2.5 7B Instruct).

Pourquoi Ollama plutôt que `transformers`/`torch` en direct :
- Inférence CPU nettement plus rapide (backend llama.cpp optimisé + modèles
  quantifiés), là où `transformers` en float32 pur peut prendre plusieurs
  minutes pour un seul CV sur une machine sans GPU.
- Mode JSON natif (`format="json"`) qui garantit une sortie syntaxiquement
  valide, ce qui élimine toute une classe de bugs de parsing.
- 100% local : Ollama tourne sur la machine (ou le serveur) de l'entreprise,
  aucune donnée candidat ne transite vers un service tiers -> conforme RGPD.

Prérequis : Ollama doit être installé et lancé localement (ollama.com), et
le modèle configuré (`OLLAMA_MODEL` dans .env) doit avoir été téléchargé au
préalable avec `ollama pull <modele>`.
"""

import json
import logging

import ollama

from config import settings
from schemas import CandidateCVData

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Levée quand le pipeline LLM échoue à produire une extraction exploitable."""


# ---------------------------------------------------------------------------
# Instructions système : décrit le schéma cible et les règles d'extraction.
# Le mode JSON d'Ollama garantit une syntaxe valide, mais pas le respect
# exact du schéma ni la qualité du contenu -> on est explicite sur les deux.
# ---------------------------------------------------------------------------
_SYSTEM_PROMPT = """Tu es un assistant spécialisé dans l'extraction d'informations structurées à partir de CV (curriculum vitae).

Analyse le texte du CV fourni par l'utilisateur et extrait UNIQUEMENT les informations qui y sont explicitement présentes. N'invente rien et ne duplique jamais une information.

Réponds STRICTEMENT avec un objet JSON respectant exactement ce schéma (aucun champ supplémentaire, aucun texte en dehors du JSON) :

{
  "nom": "string",
  "prenom": "string",
  "email": "string",
  "telephone": "string",
  "poste_vise": "string",
  "competences": ["string", "..."],
  "experiences": [
    {"poste": "string", "entreprise": "string", "periode": "string", "description": "string"}
  ],
  "formations": [
    {"diplome": "string", "etablissement": "string", "annee": "string"}
  ],
  "langues": ["string", "..."],
  "centres_interet": ["string", "..."]
}

Règles importantes :
- Chaque expérience professionnelle distincte du CV doit apparaître UNE SEULE FOIS dans "experiences", avec son propre poste/entreprise/periode/description. Ne répète jamais la même expérience plusieurs fois.
- Chaque formation distincte doit apparaître UNE SEULE FOIS dans "formations".
- "poste_vise" correspond à l'intitulé du poste recherché par le candidat (souvent en haut du CV), pas à un poste occupé dans le passé.
- Si une information n'est pas présente dans le texte, laisse le champ vide ("" pour une chaîne, [] pour une liste) plutôt que d'inventer une valeur.
"""


def _call_ollama(raw_text: str) -> str:
    """Appelle le modèle via Ollama et retourne le contenu brut de sa réponse."""
    truncated_text = raw_text[: settings.MAX_INPUT_CHARS]

    client = ollama.Client(host=settings.OLLAMA_HOST)

    try:
        response = client.chat(
            model=settings.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": truncated_text},
            ],
            format="json",  # garantit une sortie JSON syntaxiquement valide
            options={"temperature": 0},  # extraction déterministe, pas de créativité
        )
    except Exception as exc:
        raise ExtractionError(
            f"Échec de l'appel au modèle Ollama '{settings.OLLAMA_MODEL}' "
            f"(vérifiez qu'Ollama est bien lancé sur {settings.OLLAMA_HOST} "
            f"et que le modèle a été téléchargé avec 'ollama pull {settings.OLLAMA_MODEL}') : {exc}"
        ) from exc

    content = response.get("message", {}).get("content", "")
    if not content.strip():
        raise ExtractionError("Le modèle a renvoyé une réponse vide.")

    return content


def extract_cv_info(raw_text: str) -> CandidateCVData:
    """
    Exécute le pipeline d'extraction sur le texte brut d'un CV et retourne
    les données structurées et validées via Pydantic.

    Args:
        raw_text: texte brut extrait du CV (voir services/parser.py).

    Returns:
        Une instance validée de `CandidateCVData`.

    Raises:
        ExtractionError: si l'appel au modèle ou le parsing/la validation échoue.
    """
    if not raw_text.strip():
        raise ExtractionError("Texte source vide : rien à extraire.")

    content = _call_ollama(raw_text)

    try:
        parsed_json = json.loads(content)
    except json.JSONDecodeError as exc:
        # Ne devrait normalement pas arriver grâce au mode JSON natif d'Ollama,
        # mais on journalise un extrait pour pouvoir diagnostiquer si besoin.
        preview = content[:500].replace("\n", "\\n")
        logger.error("Réponse du modèle non-JSON malgré le mode JSON (extrait) : %s", preview)
        raise ExtractionError(
            f"La réponse du modèle n'est pas un JSON valide : {exc}"
        ) from exc

    try:
        return CandidateCVData.model_validate(parsed_json)
    except Exception as exc:
        raise ExtractionError(
            f"La sortie du modèle ne respecte pas le schéma attendu : {exc}"
        ) from exc