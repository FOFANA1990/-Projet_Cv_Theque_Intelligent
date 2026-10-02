"""
models.py
---------
Modèles ORM SQLAlchemy 2.0 — schéma normalisé sur plusieurs tables.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Candidate(Base):
    __tablename__ = "informations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    nom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prenom: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    telephone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    poste_vise: Mapped[str | None] = mapped_column(Text, nullable=True)

    nom_fichier: Mapped[str] = mapped_column(String(500), nullable=False)
    taille_fichier_octets: Mapped[int] = mapped_column(BigInteger, nullable=False)
    taille_fichier_lisible: Mapped[str] = mapped_column(String(50), nullable=False)
    date_reception: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # "en_attente" -> "extraction_en_cours" -> "terminee" (ou "echec").
    # Le candidat existe en base dès la soumission (identité déclarée) ;
    # les données structurées (compétences, expériences...) n'arrivent
    # qu'une fois l'extraction IA terminée en tâche de fond.
    statut: Mapped[str] = mapped_column(String(50), nullable=False, default="en_attente")

    competences: Mapped[list["Competence"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    formations: Mapped[list["Formation"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    experiences: Mapped[list["Experience"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    langues: Mapped[list["Langue"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    centres_interet: Mapped[list["CentreInteret"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    certifications: Mapped[list["Certification"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")
    adresses: Mapped[list["Adresse"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Candidate id={self.id} email={self.email!r}>"


class Competence(Base):
    __tablename__ = "competences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    competence: Mapped[str] = mapped_column(Text, nullable=False)
    candidate: Mapped["Candidate"] = relationship(back_populates="competences")


class Formation(Base):
    __tablename__ = "formations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    diplome: Mapped[str] = mapped_column(Text, default="")
    etablissement: Mapped[str] = mapped_column(Text, default="")
    annee: Mapped[str] = mapped_column(String(50), default="")
    candidate: Mapped["Candidate"] = relationship(back_populates="formations")


class Experience(Base):
    __tablename__ = "experiences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    poste: Mapped[str] = mapped_column(Text, default="")
    entreprise: Mapped[str] = mapped_column(Text, default="")
    periode: Mapped[str] = mapped_column(String(255), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    candidate: Mapped["Candidate"] = relationship(back_populates="experiences")


class Langue(Base):
    __tablename__ = "langues"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    langue: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate: Mapped["Candidate"] = relationship(back_populates="langues")


class CentreInteret(Base):
    __tablename__ = "centres_interet"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    centre_interet: Mapped[str] = mapped_column(Text, nullable=False)
    candidate: Mapped["Candidate"] = relationship(back_populates="centres_interet")


class Certification(Base):
    __tablename__ = "certifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    nom: Mapped[str] = mapped_column(Text, default="")
    organisme: Mapped[str] = mapped_column(Text, default="")
    annee: Mapped[str] = mapped_column(String(50), default="")
    candidate: Mapped["Candidate"] = relationship(back_populates="certifications")


class Pays(Base):
    __tablename__ = "pays"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nom: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    villes: Mapped[list["Ville"]] = relationship(back_populates="pays")


class Ville(Base):
    __tablename__ = "villes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nom: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    pays_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pays.id", ondelete="SET NULL"), nullable=True, index=True)
    pays: Mapped["Pays | None"] = relationship(back_populates="villes")
    adresses: Mapped[list["Adresse"]] = relationship(back_populates="ville")


class Adresse(Base):
    __tablename__ = "adresses"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    rue: Mapped[str] = mapped_column(Text, default="")
    code_postal: Mapped[str] = mapped_column(String(20), default="")
    ville_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("villes.id", ondelete="SET NULL"), nullable=True, index=True)
    pays_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pays.id", ondelete="SET NULL"), nullable=True, index=True)
    candidate: Mapped["Candidate"] = relationship(back_populates="adresses")
    ville: Mapped["Ville | None"] = relationship(back_populates="adresses")
    pays: Mapped["Pays | None"] = relationship()


class Mission(Base):
    """
    Table 'Missions' : une demande de mission soumise par un recruteur.
    Le matching n'est PAS déclenché automatiquement — il est lancé
    manuellement par un administrateur depuis le back-office
    (statut initial "en_attente").
    """

    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    societe: Mapped[str] = mapped_column(String(255), nullable=False)
    nom_recruteur: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email_recruteur: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    engagement: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expertise: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    demarrage: Mapped[str | None] = mapped_column(String(255), nullable=True)

    nom_fichier_mission: Mapped[str] = mapped_column(String(500), nullable=False)
    description_texte: Mapped[str] = mapped_column(Text, nullable=False, default="")

    date_reception: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # "en_attente" : mission reçue, matching pas encore lancé (nouveau défaut)
    # "en_cours"   : matching en cours d'exécution (déclenché par un admin)
    # "terminee"   : résultats disponibles
    # "echec"      : le matching a rencontré une erreur
    statut: Mapped[str] = mapped_column(String(50), nullable=False, default="en_attente")

    resultats: Mapped[list["MissionMatch"]] = relationship(back_populates="mission", cascade="all, delete-orphan")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mission id={self.id} societe={self.societe!r} statut={self.statut!r}>"


class MissionMatch(Base):
    __tablename__ = "mission_matches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mission_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("informations.id", ondelete="CASCADE"), nullable=False, index=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    justification: Mapped[str] = mapped_column(Text, default="")
    mission: Mapped["Mission"] = relationship(back_populates="resultats")
    candidate: Mapped["Candidate"] = relationship()