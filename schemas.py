"""
schemas.py
----------
Schémas Pydantic V2.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Experience(BaseModel):
    poste: str = ""
    entreprise: str = ""
    periode: str = ""
    description: str = ""


class Formation(BaseModel):
    diplome: str = ""
    etablissement: str = ""
    annee: str = ""


class Certification(BaseModel):
    nom: str = ""
    organisme: str = ""
    annee: str = ""


class Adresse(BaseModel):
    rue: str = ""
    code_postal: str = ""
    ville: str = ""
    pays: str = ""


class CandidateCVData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nom: str = ""
    prenom: str = ""
    email: str = ""
    telephone: str = ""
    poste_vise: str = ""
    competences: list[str] = Field(default_factory=list)
    experiences: list[Experience] = Field(default_factory=list)
    formations: list[Formation] = Field(default_factory=list)
    langues: list[str] = Field(default_factory=list)
    centres_interet: list[str] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    adresse: Adresse = Field(default_factory=Adresse)


class FileMetadata(BaseModel):
    nom_fichier: str
    taille_fichier_octets: int
    taille_fichier_lisible: str
    date_reception: datetime


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    nom: str | None = None
    prenom: str | None = None
    email: str | None = None
    telephone: str | None = None
    poste_vise: str | None = None
    donnees_extraites: CandidateCVData
    metadata_fichier: FileMetadata


class UploadSuccessResponse(BaseModel):
    success: bool = True
    message: str = "Candidature reçue et traitée avec succès."
    data: CandidateResponse


class CVSubmitResponse(BaseModel):
    """
    Accusé de réception renvoyé immédiatement au candidat après dépôt de son
    CV. L'extraction IA n'a pas encore eu lieu à ce stade (elle se fait en
    tâche de fond) — cette réponse ne contient donc aucune donnée extraite,
    seulement la confirmation que le dépôt a bien été pris en compte.
    """

    success: bool = True
    message: str = "Votre CV a bien été reçu. Un e-mail de confirmation vous a été envoyé."
    candidate_id: uuid.UUID


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    detail: str | None = None


class MissionSearchRequest(BaseModel):
    """Critères utilisés en interne pour le scoring (construits depuis une Mission)."""
    societe: str = Field(..., min_length=1)
    nom: str = ""
    email: str = ""
    engagement: str = ""
    expertise: str
    demarrage: str = ""
    message: str = Field(..., min_length=1)


class CandidateMatch(BaseModel):
    id: uuid.UUID
    nom: str | None = None
    prenom: str | None = None
    email: str | None = None
    telephone: str | None = None
    poste_vise: str | None = None
    competences: list[str] = Field(default_factory=list)
    score: int = Field(..., ge=0, le=100)
    justification: str = ""


class MatchingResponse(BaseModel):
    success: bool = True
    total_candidats_analyses: int
    resultats: list[CandidateMatch]


class MissionSubmitResponse(BaseModel):
    success: bool = True
    message: str = "Votre mission a bien été reçue. Un e-mail de confirmation vous a été envoyé."
    mission_id: uuid.UUID


class MissionAdminSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    societe: str
    nom_recruteur: str | None = None
    email_recruteur: str
    expertise: str
    engagement: str | None = None
    demarrage: str | None = None
    date_reception: datetime
    statut: str


class MissionAdminDetail(MissionAdminSummary):
    nom_fichier_mission: str
    description_texte: str
    resultats: list[CandidateMatch] = Field(default_factory=list)


class MissionTriggerResponse(BaseModel):
    """Accusé de déclenchement du matching (admin) — le calcul se fait en tâche de fond."""

    success: bool = True
    message: str = "Le matching a été lancé en tâche de fond."
    mission_id: uuid.UUID
    statut: str


class OffreEmploiExterne(BaseModel):
    """Une offre d'emploi externe trouvée via l'API France Travail."""

    intitule: str
    entreprise: str = "Entreprise non précisée"
    lieu: str = ""
    type_contrat: str = ""
    date_creation: str = ""
    url: str = ""
    description_courte: str = ""


class OffresExternesResponse(BaseModel):
    """Résultats de la recherche d'offres externes (France Travail) pour une mission."""

    success: bool = True
    source: str = "France Travail"
    mots_cles_utilises: str
    total: int
    offres: list[OffreEmploiExterne] = Field(default_factory=list)