# CV-Thèque Intelligente — API Backend

API FastAPI qui reçoit des CV depuis un formulaire HTML, extrait les informations
via le modèle IA local **NuExtract-1.5-tiny**, stocke le résultat dans PostgreSQL
et envoie un accusé de réception par e-mail.

## 1. Dépendances système (obligatoires, hors pip)

```bash
# Ubuntu/Debian
sudo apt-get install postgresql tesseract-ocr tesseract-ocr-fra poppler-utils
```

- `tesseract-ocr` : requis par `pytesseract` (OCR images + PDF scannés).
- `poppler-utils` : requis par `pdf2image` (conversion PDF -> images pour l'OCR).
- `postgresql` : base de données cible.

## 2. Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

> ⚠️ `torch` + `transformers` représentent plusieurs centaines de Mo à télécharger.
> Le premier appel à l'endpoint d'upload déclenchera aussi le téléchargement des
> poids de `numind/NuExtract-1.5-tiny` depuis Hugging Face (mis en cache ensuite
> dans `HF_CACHE_DIR`) — prévoyez donc un accès réseau sortant vers
> `huggingface.co` au premier lancement, même si l'inférence elle-même est ensuite
> 100% locale/hors-ligne.

## 3. Configuration

```bash
cp .env.example .env
# éditez .env : DATABASE_URL, CORS_ORIGINS, identifiants SMTP, etc.
```

Créez la base :
```bash
createdb cv_theque
```

## 4. Lancement

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- Documentation interactive : http://localhost:8000/docs
- Health check : http://localhost:8000/api/v1/health

## 5. Intégration avec votre formulaire HTML

```javascript
const formData = new FormData();
formData.append("file", fileInput.files[0]);

const response = await fetch("http://localhost:8000/api/v1/cv/upload", {
  method: "POST",
  body: formData,
});
const result = await response.json();
```

Pensez à renseigner l'origine exacte de votre page HTML dans `CORS_ORIGINS`
(dans `.env`), par exemple `["http://127.0.0.1:5500"]` si vous utilisez
l'extension Live Server de VS Code.

## 6. Ce qui a été vérifié dans cet environnement de génération

Le code a été testé de bout en bout dans ce sandbox, avec de vraies instances
locales de PostgreSQL et Tesseract-OCR (installées à la volée) :

| Composant | Test effectué | Résultat |
|---|---|---|
| Syntaxe de tous les fichiers `.py` | `py_compile` | ✅ OK |
| Import complet de l'app FastAPI | `import main` | ✅ OK, 3 routes exposées |
| `database.init_db()` sur PostgreSQL réel | Création de la table `candidates` (colonnes + JSONB) | ✅ OK |
| `services/parser.py` — DOCX | Extraction d'un vrai `.docx` généré avec `python-docx` | ✅ Texte correctement extrait |
| `services/parser.py` — Image/OCR | Extraction OCR (`pytesseract`) sur une image PNG générée | ✅ Texte correctement extrait |
| Endpoint `POST /api/v1/cv/upload` (succès) | Upload réel d'un `.docx`, avec `extract_cv_info` mocké (voir note ci-dessous) | ✅ HTTP 200, ligne persistée en base PostgreSQL, JSON de réponse conforme au schéma |
| Endpoint `POST /api/v1/cv/upload` (erreurs) | Extension non supportée, fichier vide | ✅ HTTP 400 avec message clair |
| `services/mail_service.py` | Tentative d'envoi réel via SMTP (sans identifiants valides dans cet environnement) | ✅ Échec capturé et journalisé sans faire planter la requête (comportement voulu) |
| `GET /api/v1/health` | Health check | ✅ HTTP 200 |

**Note importante — `services/extractor.py` (NuExtract) n'a PAS pu être testé
avec le vrai modèle** : cet environnement de génération de code n'a pas accès
réseau à `huggingface.co` (uniquement PyPI/npm/GitHub sont autorisés), donc les
poids de `numind/NuExtract-1.5-tiny` ne peuvent pas y être téléchargés. Le code
d'inférence a été relu attentivement (format de prompt `<|input|>/<|output|>`
recommandé par NuExtract, décodage JSON avec fallback de récupération, gestion
d'erreurs) mais je recommande de le tester avec le vrai modèle sur votre poste
(avec accès internet) dès le premier lancement — c'est le seul module non
validé par exécution réelle dans ce livrable.

## 7. Points d'attention pour la production

- Remplacez `Base.metadata.create_all()` (dans `init_db`) par de vraies
  migrations **Alembic** pour tout changement de schéma ultérieur.
- Restreignez `CORS_ORIGINS` à vos domaines réels (évitez `["*"]` en prod).
- Le modèle NuExtract est chargé en mémoire une seule fois par processus
  (singleton thread-safe) : prévoyez un warm-up au démarrage si vous voulez
  éviter une latence sur la toute première requête.
- Pour Gmail SMTP, utilisez un "mot de passe d'application" (pas votre mot de
  passe principal).
