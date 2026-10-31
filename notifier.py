"""
E_pharma - Notificateur de signalements (QFieldCloud -> IA -> Telegram texte + voix)

Fonctionnement :
  1. Télécharge le GeoPackage du projet depuis QFieldCloud
  2. Repère les signalements pas encore notifiés (mémorisés dans etat.json)
  3. Fait rédiger un résumé par l'IA (Claude) - ou un résumé simple si pas de clé
  4. Génère un message vocal en français (gTTS)
  5. Envoie texte + voix (+ photo si présente) sur Telegram

Variables d'environnement (secrets GitHub) :
  QFC_URL            ex. https://app.qfield.cloud/api/v1/
  QFC_USERNAME       identifiant QFieldCloud
  QFC_PASSWORD       mot de passe QFieldCloud
  QFC_PROJECT        nom du projet sur QFieldCloud (ex. E_pharma_QField)
  TELEGRAM_TOKEN     jeton du bot (fourni par @BotFather)
  TELEGRAM_CHAT_ID   identifiant de la conversation ou du groupe
  ANTHROPIC_API_KEY  (optionnel) clé API Claude pour le résumé intelligent
  ANTHROPIC_MODEL    (optionnel) défaut : claude-haiku-5-5
  LOCAL_GPKG         (optionnel, tests) chemin d'un GeoPackage local : pas d'accès QFieldCloud
"""
import json
import os
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

ETAT = Path(__file__).with_name("etat.json")
GPKG_NOM = "E_pharma_terrain.gpkg"


# ---------------------------------------------------------------- QFieldCloud
def telecharger_projet(dossier: Path) -> Path:
    local = os.environ.get("LOCAL_GPKG")
    if local:
        return Path(local)
    from qfieldcloud_sdk import sdk

    client = sdk.Client(url=os.environ.get("QFC_URL", "https://app.qfield.cloud/api/v1/"))
    client.login(os.environ["QFC_USERNAME"], os.environ["QFC_PASSWORD"])
    nom = os.environ.get("QFC_PROJECT", "E_pharma_QField")
    projets = [p for p in client.list_projects() if p["name"] == nom]
    if not projets:
        sys.exit(f"Projet '{nom}' introuvable sur QFieldCloud")
    pid = projets[0]["id"]
    # le GeoPackage + les pièces jointes (photos, audio)
    client.download_project(pid, str(dossier), filter_glob="*", force_download=True)
    trouves = list(dossier.rglob(GPKG_NOM))
    if not trouves:
        sys.exit(f"{GPKG_NOM} absent du projet QFieldCloud")
    return trouves[0]


# ---------------------------------------------------------------- GeoPackage
def point_gpkg(blob):
    """Lit un point dans une géométrie GeoPackage (en-tête GP + WKB)."""
    if not blob or blob[:2] != b"GP":
        return None
    flags = blob[3]
    env = (flags >> 1) & 0x07
    taille_env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(env, 0)
    wkb = blob[8 + taille_env:]
    ordre = "<" if wkb[0] == 1 else ">"
    x, y = struct.unpack(ordre + "dd", wkb[5:21])
    return x, y


def lire_signalements(gpkg: Path):
    con = sqlite3.connect(gpkg)
    con.row_factory = sqlite3.Row
    pharmas = {
        r["uuid"]: r for r in con.execute("SELECT * FROM pharmacies")
    }
    lignes = []
    for r in con.execute("SELECT * FROM signalements ORDER BY date_heure"):
        d = dict(r)
        geom = None
        for k in list(d):
            if isinstance(d[k], bytes) and d[k][:2] == b"GP":
                geom = point_gpkg(d.pop(k))
        d["xy"] = geom
        ph = pharmas.get(d.get("pharmacie_uuid"))
        if ph is not None:
            nom = ph["nom"] or f"Pharmacie de {ph['LOCALITE']}"
            d["pharmacie"] = {"nom": nom, "telephone": ph["telephone"], "localite": ph["LOCALITE"]}
        lignes.append(d)
    con.close()
    return lignes


def vers_latlon(xy):
    if not xy:
        return None
    from pyproj import Transformer

    t = Transformer.from_crs("EPSG:32628", "EPSG:4326", always_xy=True)
    lon, lat = t.transform(*xy)
    return round(lat, 6), round(lon, 6)


# ---------------------------------------------------------------- Résumé
def resume_simple(s):
    morceaux = [f"Nouveau signalement : {s.get('type') or 'type non précisé'}"]
    if s.get("pharmacie"):
        morceaux.append(f"concernant {s['pharmacie']['nom']}")
    if s.get("quartier"):
        morceaux.append(f"dans le secteur {s['quartier']}")
    txt = ", ".join(morceaux) + "."
    if s.get("medicament"):
        txt += f" Médicament concerné : {s['medicament']}."
    if s.get("description"):
        txt += f" Détails : {s['description']}."
    txt += f" Urgence {str(s.get('urgence') or 'non précisée').lower()}."
    if s.get("auteur"):
        txt += f" Signalé par {s['auteur']}."
    return txt


def resume_ia(s, historique):
    cle = os.environ.get("ANTHROPIC_API_KEY")
    if not cle:
        return resume_simple(s)
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=cle)
        donnees = {k: v for k, v in s.items() if k not in ("xy",)}
        meme_zone = [h for h in historique if h.get("quartier") == s.get("quartier") and h.get("uuid") != s.get("uuid")]
        consigne = (
            "Tu assistes le gestionnaire d'une plateforme de cartographie participative sur les pharmacies "
            "des Parcelles Assainies (Dakar). Rédige en français un message court (3 phrases maximum, "
            "sans liste ni emoji, à lire à voix haute) qui explique le signalement reçu : quoi, où, "
            "quelle pharmacie, niveau d'urgence, et ce qu'il faudrait faire en priorité. "
            f"Il y a déjà {len(meme_zone)} autre(s) signalement(s) dans le même secteur : mentionne-le s'il y en a.\n\n"
            f"Signalement : {json.dumps(donnees, ensure_ascii=False, default=str)}"
        )
        rep = client.messages.create(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5"),
            max_tokens=300,
            messages=[{"role": "user", "content": consigne}],
        )
        return rep.content[0].text.strip()
    except Exception as e:  # l'IA ne doit jamais bloquer la notification
        print("IA indisponible :", e)
        return resume_simple(s)


# ---------------------------------------------------------------- Voix + Telegram
def voix(texte, dossier: Path):
    from gtts import gTTS

    mp3 = dossier / "message.mp3"
    ogg = dossier / "message.ogg"
    gTTS(texte, lang="fr").save(str(mp3))
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3), "-c:a", "libopus", str(ogg)], check=True)
    return ogg


def telegram(methode, data=None, files=None):
    url = f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/{methode}"
    data = dict(data or {}, chat_id=os.environ["TELEGRAM_CHAT_ID"])
    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()


def notifier(s, texte, dossier_projet: Path, dossier_tmp: Path):
    ll = vers_latlon(s.get("xy"))
    lien = f"\n📍 https://www.openstreetmap.org/?mlat={ll[0]}&mlon={ll[1]}#map=19/{ll[0]}/{ll[1]}" if ll else ""
    entete = f"🔔 {s.get('type') or 'Signalement'} — urgence {s.get('urgence') or '?'}\n"
    telegram("sendMessage", {"text": entete + texte + lien, "disable_web_page_preview": "true"})
    with open(voix(texte, dossier_tmp), "rb") as f:
        telegram("sendVoice", files={"voice": f})
    if s.get("photo"):
        photo = next(dossier_projet.rglob(Path(s["photo"]).name), None)
        if photo and photo.exists():
            with open(photo, "rb") as f:
                telegram("sendPhoto", {"caption": "Photo jointe au signalement"}, files={"photo": f})


# ---------------------------------------------------------------- Programme
def main():
    etat = json.loads(ETAT.read_text()) if ETAT.exists() else {"notifies": []}
    deja = set(etat["notifies"])
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        gpkg = telecharger_projet(tmp / "projet")
        sigs = lire_signalements(gpkg)
        nouveaux = [s for s in sigs if s.get("uuid") and s["uuid"] not in deja]
        print(f"{len(sigs)} signalement(s), {len(nouveaux)} nouveau(x)")
        for s in nouveaux:
            texte = resume_ia(s, sigs)
            print("->", texte)
            if os.environ.get("TELEGRAM_TOKEN"):
                notifier(s, texte, gpkg.parent, tmp)
            deja.add(s["uuid"])
    etat["notifies"] = sorted(deja)
    ETAT.write_text(json.dumps(etat, indent=1))


if __name__ == "__main__":
    main()
