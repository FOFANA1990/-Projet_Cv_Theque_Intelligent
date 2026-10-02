"""
Diagnostique un problème d'encodage/caractère non-ASCII dans le fichier
.env — utile pour l'erreur :

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 ...

Placez ce fichier DANS LE MÊME DOSSIER que votre .env (normalement, le
même dossier que main.py / config.py), puis lancez :

    python diagnostiquer_env.py
"""

from pathlib import Path

CHEMIN_ENV = Path(__file__).resolve().parent / ".env"


def main() -> None:
    print(f"Dossier de ce script : {Path(__file__).resolve().parent}")
    print(f"Fichier .env recherché : {CHEMIN_ENV}\n")

    if not CHEMIN_ENV.exists():
        print("INTROUVABLE à cet endroit précis.")
        print("-> Déplacez ce script dans le dossier qui contient réellement le .env utilisé par uvicorn")
        print("   (celui d'où vous lancez 'uvicorn main:app', pas forcément celui du venv).")
        return

    contenu_brut = CHEMIN_ENV.read_bytes()
    print(f"Taille du fichier : {len(contenu_brut)} octets\n")

    try:
        texte = contenu_brut.decode("utf-8")
    except UnicodeDecodeError as erreur:
        print(f"ÉCHEC du décodage UTF-8 global : {erreur}\n")
        _localiser_octet_fautif(contenu_brut)
        return

    print("Le fichier est un UTF-8 valide de bout en bout. ✔\n")

    for ligne in texte.splitlines():
        if ligne.strip().startswith("DATABASE_URL"):
            print(f"Ligne DATABASE_URL trouvée : {ligne}")
            caracteres_non_ascii = [c for c in ligne if ord(c) > 127]
            if caracteres_non_ascii:
                print(f"\nATTENTION : caractères non-ASCII détectés : {caracteres_non_ascii}")
                print("psycopg2 ne supporte pas les caractères accentués dans la chaîne de")
                print("connexion (ni dans le mot de passe, ni ailleurs), même si le fichier")
                print("est en UTF-8 valide.")
                print("-> Changez ce caractère dans le mot de passe PostgreSQL ET dans ce .env.")
            else:
                print("DATABASE_URL ne contient que des caractères ASCII. ✔")
                print("Si l'erreur persiste malgré tout : vérifiez qu'il n'existe pas un AUTRE")
                print("fichier .env, dans un AUTRE dossier, que uvicorn lirait à la place de celui-ci.")


def _localiser_octet_fautif(contenu_brut: bytes) -> None:
    lignes = contenu_brut.split(b"\n")
    for numero, ligne in enumerate(lignes, start=1):
        try:
            ligne.decode("utf-8")
        except UnicodeDecodeError as erreur:
            print(f"--> Ligne {numero} : octet non-UTF-8 à la position {erreur.start} de la ligne")
            print(f"    Octets bruts : {ligne!r}")
            octet_fautif = ligne[erreur.start]
            try:
                car_cp1252 = bytes([octet_fautif]).decode("cp1252")
                print(f"    Interprété en CP1252 (Windows/ANSI), cet octet est : {car_cp1252!r}")
            except Exception:
                pass
            print()

    print("SOLUTION :")
    print("  1. Changez le mot de passe PostgreSQL pour un mot de passe 100% ASCII")
    print("     (lettres non accentuées + chiffres uniquement).")
    print("  2. Mettez à jour DATABASE_URL dans CE fichier .env avec le nouveau mot de passe.")
    print("  3. Vérifiez l'encodage du fichier dans VS Code (indicateur en bas à droite) :")
    print("     doit afficher 'UTF-8', pas 'ANSI' ni 'Windows-1252'.")


if __name__ == "__main__":
    main()
