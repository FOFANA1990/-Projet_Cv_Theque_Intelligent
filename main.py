"""
main.py
-------
API FastAPI "CV-Thèque Intelligente".
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, selectinload

from config import settings
from database import SessionLocal, get_db, init_db
from models import (
    Adresse,
    Candidate,
    CentreInteret,
    Certification,
    Competence,
    Experience,
    Formation,
    Langue,
    Mission,
    MissionMatch,
    Pays,
    Ville,
)
from schemas import (
    CandidateCVData,
    CandidateMatch,
    CandidateResponse,
    CVSubmitResponse,
    ErrorResponse,
    FileMetadata,
    MissionAdminDetail,
    MissionAdminSummary,
    MissionSubmitResponse,
    OffresExternesResponse,
    UploadSuccessResponse,
)
from services.extractor import ExtractionError, extract_cv_info
from services.france_travail import FranceTravailError, FranceTravailNotConfiguredError, search_offres
from services.mail_service import send_confirmation_email, send_mission_confirmation_email
from services.matching import process_mission_matching
from services.parser import (
    SUPPORTED_EXTENSIONS,
    TextExtractionError,
    UnsupportedFileError,
    extract_text_from_file,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


class UTF8JSONResponse(JSONResponse):
    """Force 'charset=utf-8' explicite (évite les problèmes d'encodage côté clients comme PowerShell)."""
    media_type = "application/json; charset=utf-8"


app = FastAPI(
    title=settings.APP_NAME,
    version="1.0.0",
    description="API backend de gestion de candidatures : extraction IA locale des CV, matching et stockage PostgreSQL.",
    default_response_class=UTF8JSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup() -> None:
    logger.info("Démarrage de %s (env=%s)", settings.APP_NAME, settings.APP_ENV)
    init_db()


def _get_or_create_pays(db: Session, nom: str) -> Pays | None:
    nom = (nom or "").strip()
    if not nom:
        return None
    pays = db.query(Pays).filter(Pays.nom.ilike(nom)).first()
    if pays is None:
        pays = Pays(nom=nom)
        db.add(pays)
        db.flush()
    return pays


def _get_or_create_ville(db: Session, nom: str, pays: Pays | None) -> Ville | None:
    nom = (nom or "").strip()
    if not nom:
        return None
    query = db.query(Ville).filter(Ville.nom.ilike(nom))
    query = query.filter(Ville.pays_id == pays.id) if pays else query.filter(Ville.pays_id.is_(None))
    ville = query.first()
    if ville is None:
        ville = Ville(nom=nom, pays_id=pays.id if pays else None)
        db.add(ville)
        db.flush()
    return ville


def _human_readable_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} TB"


@app.get("/", tags=["Santé"])
def read_root() -> dict:
    return {"status": "ok", "service": settings.APP_NAME}


@app.get("/api/v1/health", tags=["Santé"])
def health_check() -> dict:
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


async def process_cv_extraction(candidate_id: uuid.UUID, file_bytes: bytes, filename: str) -> None:
    """
    Tâche de fond complète pour un CV déposé : extrait le texte, l'analyse
    via le LLM local, puis complète la fiche candidat déjà créée en base.

    Les valeurs déclarées par le candidat à la soumission (prénom, nom,
    e-mail) ne servent qu'à la création initiale de la fiche et à l'envoi
    immédiat de l'accusé de réception (voir `submit_cv`) — une fois
    l'extraction terminée, ce sont les valeurs EXTRAITES DU CV qui
    deviennent la référence en base (le candidat peut se tromper en les
    tapant, ou son CV peut différer). Si l'IA ne trouve rien pour un champ,
    la valeur déclarée est conservée en repli plutôt que d'être effacée.

    Utilise sa propre session DB (indépendante de celle de la requête HTTP,
    déjà terminée) — même principe que `process_mission_matching`.
    """
    db = SessionLocal()
    try:
        candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
        if candidate is None:
            logger.error("Candidat %s introuvable pour l'extraction en tâche de fond.", candidate_id)
            return

        candidate.statut = "extraction_en_cours"
        db.commit()

        try:
            raw_text = await asyncio.to_thread(extract_text_from_file, file_bytes, filename)
        except (UnsupportedFileError, TextExtractionError) as exc:
            logger.error("Échec d'extraction de texte pour le candidat %s : %s", candidate_id, exc)
            candidate.statut = "echec"
            db.commit()
            return

        try:
            extracted: CandidateCVData = await asyncio.to_thread(extract_cv_info, raw_text)
        except ExtractionError as exc:
            logger.error("Échec de l'extraction IA pour le candidat %s : %s", candidate_id, exc)
            candidate.statut = "echec"
            db.commit()
            return

        # Une fois l'extraction terminée, les valeurs EXTRAITES DU CV
        # deviennent la référence en base (le candidat peut se tromper en
        # les tapant, ou son CV peut légitimement différer de ce qu'il a
        # saisi) — sauf si l'IA n'a rien trouvé pour un champ donné, où l'on
        # garde alors la valeur déclarée à la soumission comme repli plutôt
        # que de perdre l'information de contact.
        candidate.nom = extracted.nom or candidate.nom
        candidate.prenom = extracted.prenom or candidate.prenom
        candidate.email = extracted.email or candidate.email
        candidate.telephone = extracted.telephone or None
        candidate.poste_vise = extracted.poste_vise or None
        candidate.competences = [Competence(competence=c) for c in extracted.competences if c]
        candidate.experiences = [
            Experience(poste=e.poste, entreprise=e.entreprise, periode=e.periode, description=e.description)
            for e in extracted.experiences
        ]
        candidate.formations = [
            Formation(diplome=f.diplome, etablissement=f.etablissement, annee=f.annee) for f in extracted.formations
        ]
        candidate.langues = [Langue(langue=l) for l in extracted.langues if l]
        candidate.centres_interet = [CentreInteret(centre_interet=c) for c in extracted.centres_interet if c]
        candidate.certifications = [
            Certification(nom=c.nom, organisme=c.organisme, annee=c.annee) for c in extracted.certifications if c.nom
        ]

        adresse_data = extracted.adresse
        if adresse_data.rue or adresse_data.ville or adresse_data.pays or adresse_data.code_postal:
            pays = _get_or_create_pays(db, adresse_data.pays)
            ville = _get_or_create_ville(db, adresse_data.ville, pays)
            candidate.adresses = [
                Adresse(
                    rue=adresse_data.rue, code_postal=adresse_data.code_postal,
                    ville_id=ville.id if ville else None, pays_id=pays.id if pays else None,
                )
            ]

        candidate.statut = "terminee"
        db.commit()
        logger.info("Extraction terminée pour le candidat %s.", candidate_id)
    except Exception:
        db.rollback()
        logger.exception("Échec inattendu de l'extraction en tâche de fond pour le candidat %s", candidate_id)
        try:
            candidate = db.query(Candidate).filter(Candidate.id == candidate_id).first()
            if candidate is not None:
                candidate.statut = "echec"
                db.commit()
        except Exception:
            db.rollback()
            logger.exception("Échec supplémentaire lors de la mise à jour du statut d'échec pour %s", candidate_id)
    finally:
        db.close()


@app.post(
    "/api/v1/cv/upload",
    response_model=CVSubmitResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Candidatures"],
    summary="Reçoit un CV + identité déclarée, notifie immédiatement le candidat, puis analyse en tâche de fond",
)
async def submit_cv(
    background_tasks: BackgroundTasks,
    prenom: str = Form(...),
    nom: str = Form(...),
    email: str = Form(...),
    file: UploadFile = File(..., description="Fichier CV (.pdf, .docx, .png, .jpg, .jpeg)"),
    db: Session = Depends(get_db),
) -> CVSubmitResponse:
    """
    1. Validation rapide du fichier (extension, taille) — pas d'extraction ici.
    2. Création immédiate de la fiche candidat (identité déclarée : prénom,
       nom, e-mail) avec statut "en_attente".
    3. Notification du candidat par e-mail, IMMÉDIATEMENT (tâche de fond,
       mais programmée avant même l'extraction — le candidat n'attend pas
       l'analyse IA pour recevoir sa confirmation).
    4. Extraction du texte + analyse IA + complétion de la fiche candidat
       (compétences, expériences...), en tâche de fond
       (`process_cv_extraction`) — peut prendre plusieurs minutes.
    """
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun nom de fichier fourni.")

    extension = Path(file.filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Extension '{extension}' non supportée. Formats acceptés : {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    file_bytes = await file.read()
    taille_octets = len(file_bytes)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if taille_octets > max_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Fichier trop volumineux (max {settings.MAX_UPLOAD_SIZE_MB} MB).")
    if taille_octets == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le fichier envoyé est vide.")

    try:
        candidate = Candidate(
            nom=nom, prenom=prenom, email=email,
            nom_fichier=file.filename,
            taille_fichier_octets=taille_octets,
            taille_fichier_lisible=_human_readable_size(taille_octets),
            date_reception=datetime.now(timezone.utc),
            statut="en_attente",
        )
        db.add(candidate)
        db.commit()
        db.refresh(candidate)
    except Exception as exc:
        db.rollback()
        logger.exception("Échec de l'enregistrement initial du candidat pour '%s'", file.filename)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Échec de l'enregistrement en base de données : {exc}") from exc

    # Notification immédiate : programmée AVANT l'extraction, avec les
    # informations déclarées par le candidat (pas besoin d'attendre l'IA).
    background_tasks.add_task(
        send_confirmation_email,
        recipient_email=candidate.email or "",
        prenom=candidate.prenom or "",
        nom=candidate.nom or "",
        poste_vise="",
    )

    # Extraction + stockage complet : en tâche de fond, avec sa propre
    # session DB (voir process_cv_extraction).
    background_tasks.add_task(process_cv_extraction, candidate.id, file_bytes, file.filename)

    return CVSubmitResponse(candidate_id=candidate.id)


# ---------------------------------------------------------------------------
# Protection minimale de l'espace admin par clé secrète (voir avertissement
# dans config.py : pas une vraie authentification).
# ---------------------------------------------------------------------------

def require_admin_key(x_admin_key: str = Header(default="")) -> None:
    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clé admin manquante ou invalide (en-tête X-Admin-Key requis).")


@app.post(
    "/api/v1/missions/soumettre",
    response_model=MissionSubmitResponse,
    responses={400: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["Missions"],
    summary="Reçoit une demande de mission recruteur avec fichier descriptif joint",
)
async def submit_mission(
    background_tasks: BackgroundTasks,
    societe: str = Form(...),
    nom: str = Form(""),
    email: str = Form(...),
    engagement: str = Form(""),
    expertise: str = Form(...),
    demarrage: str = Form(""),
    mission_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> MissionSubmitResponse:
    """
    1. Extraction du texte du fichier descriptif joint.
    2. Sauvegarde de la mission en base (statut initial "en_attente").
    3. Notification du recruteur (accusé de réception uniquement).

    Le matching n'est PLUS déclenché automatiquement ici : il doit être
    lancé manuellement par un administrateur depuis le back-office, via
    POST /api/v1/admin/missions/{id}/lancer-matching.
    """
    if not mission_file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucun nom de fichier fourni pour le descriptif de mission.")

    extension = Path(mission_file.filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Extension '{extension}' non supportée. Formats acceptés : {', '.join(sorted(SUPPORTED_EXTENSIONS))}",
        )

    file_bytes = await mission_file.read()
    if not file_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Le fichier descriptif de mission envoyé est vide.")

    try:
        description_texte = extract_text_from_file(file_bytes, mission_file.filename)
    except UnsupportedFileError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except TextExtractionError as exc:
        logger.error("Échec d'extraction du descriptif de mission '%s' : %s", mission_file.filename, exc)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Impossible de lire le contenu du fichier descriptif : {exc}") from exc

    try:
        mission = Mission(
            societe=societe, nom_recruteur=nom or None, email_recruteur=email,
            engagement=engagement or None, expertise=expertise, demarrage=demarrage or None,
            nom_fichier_mission=mission_file.filename, description_texte=description_texte,
            statut="en_attente",
        )
        db.add(mission)
        db.commit()
        db.refresh(mission)
    except Exception as exc:
        db.rollback()
        logger.exception("Échec de l'enregistrement de la mission pour '%s'", societe)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Échec de l'enregistrement de la mission en base de données : {exc}") from exc

    background_tasks.add_task(
        send_mission_confirmation_email,
        recipient_email=mission.email_recruteur, nom_recruteur=mission.nom_recruteur or "",
        societe=mission.societe, expertise=mission.expertise,
    )

    return MissionSubmitResponse(mission_id=mission.id)


def _mission_to_admin_detail(mission: Mission) -> MissionAdminDetail:
    resultats_tries = sorted(mission.resultats, key=lambda r: r.score, reverse=True)
    candidats_scores = [
        CandidateMatch(
            id=r.candidate_id,
            nom=r.candidate.nom if r.candidate else None,
            prenom=r.candidate.prenom if r.candidate else None,
            email=r.candidate.email if r.candidate else None,
            telephone=r.candidate.telephone if r.candidate else None,
            poste_vise=r.candidate.poste_vise if r.candidate else None,
            competences=[c.competence for c in r.candidate.competences] if r.candidate else [],
            score=r.score,
            justification=r.justification,
        )
        for r in resultats_tries
    ]
    return MissionAdminDetail(
        id=mission.id, societe=mission.societe, nom_recruteur=mission.nom_recruteur,
        email_recruteur=mission.email_recruteur, expertise=mission.expertise,
        engagement=mission.engagement, demarrage=mission.demarrage,
        date_reception=mission.date_reception, statut=mission.statut,
        nom_fichier_mission=mission.nom_fichier_mission, description_texte=mission.description_texte,
        resultats=candidats_scores,
    )


@app.get(
    "/api/v1/admin/missions",
    response_model=list[MissionAdminSummary],
    dependencies=[Depends(require_admin_key)],
    tags=["Admin"],
    summary="[Admin] Liste toutes les missions soumises par les recruteurs",
)
def list_missions(db: Session = Depends(get_db)) -> list[Mission]:
    return db.query(Mission).order_by(Mission.date_reception.desc()).all()


@app.get(
    "/api/v1/admin/missions/recherche",
    response_model=list[MissionAdminSummary],
    dependencies=[Depends(require_admin_key)],
    tags=["Admin"],
    summary="[Admin] Recherche les missions dont l'expertise recherchée correspond au terme donné",
)
def search_missions(
    expertise: str = Query(..., min_length=1, description="Terme à rechercher dans le champ 'Expertise recherchée'"),
    db: Session = Depends(get_db),
) -> list[Mission]:
    return (
        db.query(Mission)
        .filter(Mission.expertise.ilike(f"%{expertise}%"))
        .order_by(Mission.date_reception.desc())
        .all()
    )


@app.get(
    "/api/v1/admin/missions/{mission_id}/resultats",
    response_model=MissionAdminDetail,
    responses={404: {"model": ErrorResponse}},
    dependencies=[Depends(require_admin_key)],
    tags=["Admin"],
    summary="[Admin] Détail d'une mission + candidats scorés (résultats déjà calculés)",
)
def get_mission_results(mission_id: uuid.UUID, db: Session = Depends(get_db)) -> MissionAdminDetail:
    mission = (
        db.query(Mission)
        .options(selectinload(Mission.resultats).selectinload(MissionMatch.candidate).selectinload(Candidate.competences))
        .filter(Mission.id == mission_id)
        .first()
    )
    if mission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission introuvable.")
    return _mission_to_admin_detail(mission)


@app.post(
    "/api/v1/admin/missions/{mission_id}/lancer-matching",
    response_model=MissionAdminDetail,
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    dependencies=[Depends(require_admin_key)],
    tags=["Admin"],
    summary="[Admin] Déclenche le matching pour une mission et retourne les résultats",
)
async def launch_mission_matching(mission_id: uuid.UUID, db: Session = Depends(get_db)) -> MissionAdminDetail:
    """
    Lance le scoring des candidats pour cette mission (peut prendre de
    quelques secondes à plusieurs minutes selon le nombre de candidats en
    base) et retourne directement les résultats une fois terminé — c'est
    cet appel que le bouton "Lancer le matching" du back-office déclenche.
    """
    mission_exists = db.query(Mission.id).filter(Mission.id == mission_id).first()
    if mission_exists is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission introuvable.")

    try:
        await process_mission_matching(mission_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Échec du matching : {exc}") from exc

    mission = (
        db.query(Mission)
        .options(selectinload(Mission.resultats).selectinload(MissionMatch.candidate).selectinload(Candidate.competences))
        .filter(Mission.id == mission_id)
        .first()
    )
    return _mission_to_admin_detail(mission)


@app.get(
    "/api/v1/admin/missions/{mission_id}/offres-similaires",
    response_model=OffresExternesResponse,
    responses={404: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    dependencies=[Depends(require_admin_key)],
    tags=["Admin"],
    summary="[Admin] Recherche des offres d'emploi externes (France Travail) pour l'expertise de la mission",
)
def search_external_offers(mission_id: uuid.UUID, db: Session = Depends(get_db)) -> OffresExternesResponse:
    """
    Interroge l'API officielle France Travail avec l'expertise recherchée de
    la mission comme mots-clés, pour donner au recruteur/à l'admin une vue
    du marché (offres comparables déjà publiées ailleurs). N'utilise QUE
    des sources légales à API ouverte — voir services/france_travail.py.
    """
    mission = db.query(Mission).filter(Mission.id == mission_id).first()
    if mission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission introuvable.")

    try:
        offres = search_offres(mission.expertise, max_resultats=10)
    except FranceTravailNotConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except FranceTravailError as exc:
        logger.error("Échec de la recherche France Travail pour la mission %s : %s", mission_id, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return OffresExternesResponse(mots_cles_utilises=mission.expertise, total=len(offres), offres=offres)