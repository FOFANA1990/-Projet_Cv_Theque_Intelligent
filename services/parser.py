"""
services/parser.py
-------------------
Extraction de texte brut à partir de différents formats de fichiers CV :
- .docx  -> python-docx
- .pdf   -> pdfplumber, avec fallback OCR (pdf2image + pytesseract) si le
            PDF est scanné (aucun texte détecté nativement)
- .png / .jpg / .jpeg -> pytesseract (Tesseract-OCR)

Toute la fonction est volontairement défensive (try/except par étape) car
un CV mal formé, corrompu ou scanné en basse qualité ne doit jamais faire
planter l'API : on préfère lever une exception métier claire
(`UnsupportedFileError` / `TextExtractionError`) que de laisser fuiter une
stacktrace brute vers le client.
"""

import io
import logging
import re
from pathlib import Path

import pdfplumber
import pytesseract
from docx import Document
from PIL import Image

from config import settings

logger = logging.getLogger(__name__)

# Configuration du chemin binaire de Tesseract si fourni (utile sous Windows,
# ou si Tesseract n'est pas dans le PATH système).
if settings.TESSERACT_CMD:
    pytesseract.pytesseract.tesseract_cmd = settings.TESSERACT_CMD

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}

# Seuil (en caractères) en dessous duquel on considère qu'un PDF n'a pas de
# texte exploitable et qu'il faut basculer sur l'OCR (cas des CV scannés).
MIN_TEXT_LENGTH_BEFORE_OCR_FALLBACK = 30


class UnsupportedFileError(Exception):
    """Levée quand l'extension du fichier n'est pas prise en charge."""


class TextExtractionError(Exception):
    """Levée quand l'extraction de texte échoue malgré les tentatives/fallbacks."""


# Motif des artefacts de type "(cid:12)" que pdfplumber produit parfois pour
# des glyphes non standards (icônes, polices custom) dans les CV designés
# (Canva, Word avec pictogrammes...).
_CID_ARTIFACT_PATTERN = re.compile(r"\(cid:\d+\)")

# Tolérance verticale (en points PDF) pour considérer que deux mots
# appartiennent à la même ligne de texte.
_LINE_Y_TOLERANCE = 3.0

# Écart horizontal minimal (en points PDF) pour considérer qu'il y a une
# vraie coupure entre deux colonnes plutôt qu'un simple espacement de mots.
_MIN_COLUMN_GAP = 40.0


def _clean_extracted_text(text: str) -> str:
    """Retire les artefacts de rendu (glyphes non standards) du texte extrait."""
    return _CID_ARTIFACT_PATTERN.sub("", text)


def _group_words_into_lines(words: list[dict]) -> list[tuple[float, str]]:
    """
    Regroupe une liste de mots pdfplumber (avec positions x0/top) en lignes
    de texte, en respectant l'ordre de lecture gauche->droite au sein de
    chaque ligne. Retourne une liste de (position_verticale, texte_ligne).
    """
    if not words:
        return []

    words_sorted = sorted(words, key=lambda w: w["top"])
    lines: list[list[dict]] = []
    current_line: list[dict] = []
    current_top: float | None = None

    for word in words_sorted:
        if current_top is None or abs(word["top"] - current_top) <= _LINE_Y_TOLERANCE:
            current_line.append(word)
            current_top = word["top"] if current_top is None else current_top
        else:
            lines.append(current_line)
            current_line = [word]
            current_top = word["top"]
    if current_line:
        lines.append(current_line)

    result = []
    for line in lines:
        line_sorted = sorted(line, key=lambda w: w["x0"])
        line_top = min(w["top"] for w in line_sorted)
        line_text = " ".join(w["text"] for w in line_sorted)
        result.append((line_top, line_text))

    return result


def _detect_column_split_x(words: list[dict], page_width: float) -> float | None:
    """
    Détecte une éventuelle coupure verticale entre deux colonnes en cherchant
    le plus grand espace horizontal vide, situé dans la zone centrale de la
    page (entre 15% et 85% de sa largeur, pour ignorer les marges).

    Retourne la position x de la coupure, ou None si aucune mise en page à
    deux colonnes claire n'est détectée (CV en une seule colonne).
    """
    if not words:
        return None

    xs = sorted(w["x0"] for w in words)
    best_gap = 0.0
    split_x: float | None = None

    for prev_x, next_x in zip(xs, xs[1:]):
        gap = next_x - prev_x
        midpoint = (prev_x + next_x) / 2
        if page_width * 0.15 < midpoint < page_width * 0.85 and gap > best_gap:
            best_gap = gap
            split_x = midpoint

    return split_x if best_gap >= _MIN_COLUMN_GAP else None


def _extract_page_text_reading_order(page) -> str:
    """
    Extrait le texte d'une page pdfplumber en respectant un ordre de lecture
    correct même en cas de mise en page à deux colonnes (CV avec barre
    latérale de coordonnées/compétences + contenu principal, très fréquent).

    Stratégie : si une coupure verticale nette est détectée, le texte de la
    colonne de gauche est reconstitué en entier (de haut en bas), suivi de
    celui de la colonne de droite — plutôt que de suivre l'ordre brut des
    objets du PDF, qui entrelace souvent les deux colonnes ligne par ligne.
    """
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False)
    if not words:
        return ""

    split_x = _detect_column_split_x(words, page.width)

    if split_x is None:
        # Page à une seule colonne : ordre de lecture standard haut -> bas.
        lines = _group_words_into_lines(words)
        lines.sort(key=lambda item: item[0])
        return "\n".join(text for _, text in lines)

    # Page à deux colonnes : on sépare les mots par colonne avant de
    # reconstituer chaque colonne indépendamment, dans son propre ordre de
    # lecture haut -> bas.
    left_words = [w for w in words if w["x0"] < split_x]
    right_words = [w for w in words if w["x0"] >= split_x]

    left_lines = sorted(_group_words_into_lines(left_words), key=lambda item: item[0])
    right_lines = sorted(_group_words_into_lines(right_words), key=lambda item: item[0])

    left_text = "\n".join(text for _, text in left_lines)
    right_text = "\n".join(text for _, text in right_lines)

    return "\n".join(part for part in (left_text, right_text) if part)


def _extract_from_docx(file_bytes: bytes) -> str:
    try:
        document = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text for p in document.paragraphs if p.text.strip()]

        # On récupère aussi le texte des tableaux (souvent utilisés dans les CV
        # pour la mise en page : compétences, coordonnées, etc.)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        paragraphs.append(cell.text.strip())

        return "\n".join(paragraphs)
    except Exception as exc:
        raise TextExtractionError(f"Échec de lecture du fichier DOCX : {exc}") from exc


def _extract_from_image_bytes(file_bytes: bytes, lang: str = "fra+eng") -> str:
    try:
        image = Image.open(io.BytesIO(file_bytes))
        return pytesseract.image_to_string(image, lang=lang)
    except Exception as exc:
        raise TextExtractionError(f"Échec de l'OCR sur l'image : {exc}") from exc


def _extract_from_pdf(file_bytes: bytes) -> str:
    # 1) Tentative d'extraction "texte natif" via pdfplumber, avec
    #    reconstruction de l'ordre de lecture (gère les CV à deux colonnes).
    text_parts: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = _extract_page_text_reading_order(page)
                if page_text.strip():
                    text_parts.append(page_text)
    except Exception as exc:
        logger.warning("pdfplumber a échoué, tentative de fallback OCR : %s", exc)

    native_text = _clean_extracted_text("\n".join(text_parts).strip())

    if len(native_text) >= MIN_TEXT_LENGTH_BEFORE_OCR_FALLBACK:
        return native_text

    # 2) Fallback OCR : le PDF est probablement scanné (image), on convertit
    #    chaque page en image puis on applique Tesseract dessus.
    logger.info("Peu/pas de texte natif détecté dans le PDF, bascule sur l'OCR.")
    try:
        from pdf2image import convert_from_bytes

        ocr_parts: list[str] = []
        pages_as_images = convert_from_bytes(file_bytes)
        for page_image in pages_as_images:
            ocr_parts.append(pytesseract.image_to_string(page_image, lang="fra+eng"))

        ocr_text = "\n".join(ocr_parts).strip()

        # On garde le meilleur résultat entre extraction native (même partielle)
        # et OCR, plutôt que de jeter l'un des deux.
        combined = (native_text + "\n" + ocr_text).strip()
        if not combined:
            raise TextExtractionError(
                "Aucun texte n'a pu être extrait du PDF (ni nativement, ni via OCR)."
            )
        return combined
    except TextExtractionError:
        raise
    except Exception as exc:
        raise TextExtractionError(f"Échec du fallback OCR sur le PDF : {exc}") from exc


def extract_text_from_file(file_bytes: bytes, filename: str) -> str:
    """
    Point d'entrée unique du parser : détecte le type de fichier via son
    extension et route vers la bonne stratégie d'extraction.

    Args:
        file_bytes: contenu binaire brut du fichier reçu.
        filename: nom original du fichier (utilisé pour déterminer l'extension).

    Returns:
        Le texte brut extrait (peut être vide si le document est vide).

    Raises:
        UnsupportedFileError: extension non supportée.
        TextExtractionError: échec de l'extraction malgré les fallbacks.
    """
    extension = Path(filename).suffix.lower()

    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedFileError(
            f"Extension '{extension}' non supportée. "
            f"Formats acceptés : {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )

    if not file_bytes:
        raise TextExtractionError("Le fichier reçu est vide (0 octet).")

    if extension == ".docx":
        text = _extract_from_docx(file_bytes)
    elif extension == ".pdf":
        text = _extract_from_pdf(file_bytes)
    else:  # .png / .jpg / .jpeg
        text = _extract_from_image_bytes(file_bytes)

    if not text.strip():
        raise TextExtractionError(
            "L'extraction n'a produit aucun texte exploitable pour ce fichier."
        )

    return text.strip()