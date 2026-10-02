"""
services/mail_service.py
-------------------------
Envoi de l'e-mail de confirmation de réception de candidature, via
FastAPI-Mail (SMTP asynchrone). Conçu pour être appelé en tâche de fond
(`BackgroundTasks`) afin de ne jamais ralentir/bloquer la réponse HTTP
de l'endpoint d'upload.
"""

import logging

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType
from fastapi_mail.schemas import MultipartSubtypeEnum

from config import settings

logger = logging.getLogger(__name__)

_mail_config = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_FROM_NAME=settings.MAIL_FROM_NAME,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=settings.MAIL_USE_CREDENTIALS,
    VALIDATE_CERTS=settings.MAIL_VALIDATE_CERTS,
)

_fast_mail = FastMail(_mail_config)


def _build_confirmation_html(prenom: str, nom: str, poste_vise: str) -> str:
    display_name = f"{prenom} {nom}".strip() or "Candidat(e)"
    poste_line = (
        f"<p>Poste visé : <strong>{poste_vise}</strong></p>" if poste_vise else ""
    )
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1f2937;">
            <h2>Confirmation de réception de votre candidature</h2>
            <p>Bonjour {display_name},</p>
            <p>
                Nous vous confirmons la bonne réception de votre CV et de votre
                candidature. Notre équipe recrutement va l'étudier avec
                attention et reviendra vers vous dans les meilleurs délais.
            </p>
            {poste_line}
            <p>Nous vous remercions pour l'intérêt que vous portez à notre entreprise.</p>
            <p>Cordialement,<br/>{settings.MAIL_FROM_NAME}</p>
        </body>
    </html>
    """


def _build_confirmation_text(prenom: str, nom: str, poste_vise: str) -> str:
    """
    Version texte brut de l'e-mail de confirmation. Les filtres anti-spam
    pénalisent fortement les e-mails envoyés en HTML seul, sans alternative
    texte (multipart/alternative) — fournir les deux versions améliore
    significativement la délivrabilité, en plus d'être plus accessible.
    """
    display_name = f"{prenom} {nom}".strip() or "Candidat(e)"
    poste_line = f"Poste visé : {poste_vise}\n" if poste_vise else ""
    return (
        f"Bonjour {display_name},\n\n"
        "Nous vous confirmons la bonne réception de votre CV et de votre "
        "candidature. Notre équipe recrutement va l'étudier avec attention "
        "et reviendra vers vous dans les meilleurs délais.\n\n"
        f"{poste_line}"
        "Nous vous remercions pour l'intérêt que vous portez à notre entreprise.\n\n"
        f"Cordialement,\n{settings.MAIL_FROM_NAME}"
    )


async def send_confirmation_email(
    recipient_email: str,
    prenom: str = "",
    nom: str = "",
    poste_vise: str = "",
) -> None:
    """
    Envoie l'e-mail de confirmation au candidat.

    Conçue pour être passée à `BackgroundTasks.add_task(...)` : toute
    exception est capturée et journalisée plutôt que propagée, car un
    échec d'envoi d'e-mail ne doit jamais faire échouer la candidature
    déjà enregistrée en base.
    """
    if not recipient_email:
        logger.warning("Aucune adresse e-mail détectée pour ce candidat, envoi ignoré.")
        return

    message = MessageSchema(
        subject="Confirmation de réception de votre candidature",
        recipients=[recipient_email],
        body=_build_confirmation_html(prenom, nom, poste_vise),
        alternative_body=_build_confirmation_text(prenom, nom, poste_vise),
        subtype=MessageType.html,
        # Requis pour que fastapi-mail conserve réellement alternative_body :
        # par défaut multipart_subtype="mixed" (pensé pour les pièces
        # jointes) et ignore silencieusement alternative_body.
        multipart_subtype=MultipartSubtypeEnum.alternative,
    )

    try:
        await _fast_mail.send_message(message)
        logger.info("E-mail de confirmation envoyé à %s", recipient_email)
    except Exception as exc:
        # On journalise mais on ne relève pas l'exception : l'échec d'envoi
        # d'e-mail ne doit jamais impacter le candidat / faire échouer la requête.
        logger.error("Échec de l'envoi de l'e-mail à %s : %s", recipient_email, exc)


def _build_mission_confirmation_html(nom_recruteur: str, societe: str, expertise: str) -> str:
    display_name = nom_recruteur.strip() or "Bonjour"
    greeting = f"Bonjour {display_name}," if nom_recruteur.strip() else "Bonjour,"
    return f"""
    <html>
        <body style="font-family: Arial, sans-serif; color: #1f2937;">
            <h2>Confirmation de réception de votre mission</h2>
            <p>{greeting}</p>
            <p>
                Nous vous confirmons la bonne réception de votre demande de mission
                pour <strong>{societe}</strong> (expertise recherchée :
                <strong>{expertise}</strong>).
            </p>
            <p>
                Notre équipe va analyser votre besoin et le rapprocher des profils
                disponibles dans notre CVthèque. Nous revenons vers vous dans les
                meilleurs délais avec les candidatures les plus pertinentes.
            </p>
            <p>Nous vous remercions pour votre confiance.</p>
            <p>Cordialement,<br/>{settings.MAIL_FROM_NAME}</p>
        </body>
    </html>
    """


def _build_mission_confirmation_text(nom_recruteur: str, societe: str, expertise: str) -> str:
    greeting = f"Bonjour {nom_recruteur.strip()}," if nom_recruteur.strip() else "Bonjour,"
    return (
        f"{greeting}\n\n"
        f"Nous vous confirmons la bonne réception de votre demande de mission "
        f"pour {societe} (expertise recherchée : {expertise}).\n\n"
        "Notre équipe va analyser votre besoin et le rapprocher des profils "
        "disponibles dans notre CVthèque. Nous revenons vers vous dans les "
        "meilleurs délais avec les candidatures les plus pertinentes.\n\n"
        "Nous vous remercions pour votre confiance.\n\n"
        f"Cordialement,\n{settings.MAIL_FROM_NAME}"
    )


async def send_mission_confirmation_email(
    recipient_email: str,
    nom_recruteur: str = "",
    societe: str = "",
    expertise: str = "",
) -> None:
    """
    Envoie l'e-mail de confirmation au recruteur après soumission d'une
    mission. Ne contient volontairement AUCUNE information sur les
    candidats — le matching se fait en tâche de fond et ses résultats sont
    réservés aux administrateurs (voir main.py, endpoints /api/v1/admin/...).
    """
    if not recipient_email:
        logger.warning("Aucune adresse e-mail recruteur fournie, envoi ignoré.")
        return

    message = MessageSchema(
        subject="Confirmation de réception de votre mission",
        recipients=[recipient_email],
        body=_build_mission_confirmation_html(nom_recruteur, societe, expertise),
        alternative_body=_build_mission_confirmation_text(nom_recruteur, societe, expertise),
        subtype=MessageType.html,
        multipart_subtype=MultipartSubtypeEnum.alternative,
    )

    try:
        await _fast_mail.send_message(message)
        logger.info("E-mail de confirmation de mission envoyé à %s", recipient_email)
    except Exception as exc:
        logger.error(
            "Échec de l'envoi de l'e-mail de confirmation de mission à %s : %s",
            recipient_email, exc,
        )