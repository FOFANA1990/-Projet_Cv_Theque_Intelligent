# Dockerfile unique, compatible Hugging Face Spaces (Docker SDK) et
# Render.com (Web Service -> Docker). Place ce fichier a la racine du
# projet cv_hf (a cote de main.py et requirements.txt).

FROM python:3.12-slim

# --- Dependances systeme pour l'OCR (ne s'installent pas via pip) ---
# tesseract-ocr-fra : paquet linguistique francais
# poppler-utils     : necessaire a pdf2image pour convertir les PDF en images
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-fra \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installe les dependances Python en premier (profite du cache Docker
# tant que requirements.txt ne change pas -> rebuilds plus rapides)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie le reste du code
COPY . .

# Cree le dossier d'upload (au cas ou .dockerignore l'exclurait)
RUN mkdir -p uploads

# Hugging Face Spaces attend le port 7860 par defaut (configure dans le
# frontmatter du README.md du Space). Render fournit sa propre variable
# PORT a l'execution. ${PORT:-7860} gere les deux cas avec un seul
# Dockerfile, sans rien a changer entre les deux plateformes.
ENV PORT=7860
EXPOSE 7860

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-7860}"]
