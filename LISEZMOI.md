# E_pharma : notifications vocales des signalements

Chaque fois qu'un agent enregistre un signalement dans QField (rupture de médicament,
pharmacie de garde, pharmacie fermée…), le gestionnaire reçoit sur Telegram :
- un **message texte** qui résume le signalement (rédigé par l'IA), avec un lien vers la carte ;
- un **message vocal** en français qui lit ce résumé ;
- la **photo**, si l'agent en a pris une.

Le script s'exécute gratuitement sur GitHub toutes les 10 minutes, même quand votre PC est éteint.

---

## Étape 1 : publier le projet sur QFieldCloud (sur votre PC)

1. Créez un compte gratuit sur https://app.qfield.cloud.
2. Dans QGIS, installez l'extension **QFieldSync** (menu *Extensions → Installer/Gérer les extensions*).
3. Ouvrez le projet `N:/PROJETS/E_pharma/QField/E_pharma_QField.qgz`.
4. Avec QFieldSync, connectez-vous à QFieldCloud, puis choisissez **Créer un nouveau projet** pour convertir le projet actuel.
   Notez le **nom exact** que porte le projet sur QFieldCloud (par exemple `E_pharma_QField`).
5. Sur le téléphone, installez **QField**, connectez-vous avec le même compte et téléchargez le projet.
   Activez la synchronisation automatique dans les paramètres du projet.

## Étape 2 : créer le bot Telegram (2 minutes)

1. Dans Telegram, ouvrez **@BotFather**, envoyez `/newbot`, choisissez un nom.
   BotFather vous donne un **jeton**, du type `123456:ABC-...`.
2. Envoyez un message quelconque à votre bot (ou ajoutez-le à un groupe et écrivez dans le groupe).
3. Ouvrez dans un navigateur : `https://api.telegram.org/bot<VOTRE_JETON>/getUpdates`
   et relevez la valeur `"chat":{"id": ...}`. C'est votre **TELEGRAM_CHAT_ID**
   (pour un groupe, il commence par `-`).

## Étape 3 : mettre le script sur GitHub

1. Créez un compte sur https://github.com, puis un **nouveau dépôt** (par exemple `epharma-notifier`).
   - Dépôt **public** : l'exécution est gratuite et illimitée. Aucun mot de passe n'est dans le code, ils sont dans les secrets.
   - Dépôt **privé** : le temps d'exécution gratuit est limité. Dans ce cas, remplacez `*/10` par `*/30` dans `.github/workflows/notifier.yml`.
2. Envoyez les fichiers de ce dossier (*Add file → Upload files*) en gardant le dossier `.github/workflows/`.
3. Dans **Settings → Secrets and variables → Actions → New repository secret**, créez :

| Secret | Valeur |
|---|---|
| `QFC_URL` | `https://app.qfield.cloud/api/v1/` |
| `QFC_USERNAME` | votre identifiant QFieldCloud |
| `QFC_PASSWORD` | votre mot de passe QFieldCloud |
| `QFC_PROJECT` | le nom exact du projet sur QFieldCloud |
| `TELEGRAM_TOKEN` | le jeton donné par BotFather |
| `TELEGRAM_CHAT_ID` | l'identifiant relevé à l'étape 2 |
| `ANTHROPIC_API_KEY` | *(optionnel)* clé API Claude, à créer sur https://console.anthropic.com. Sans clé, un résumé simple est envoyé. |

4. Onglet **Actions → Notifier les signalements E_pharma → Run workflow** pour un premier test.

## Étape 4 : tester

1. Dans QField, ajoutez un point dans **Signalements**, par exemple « Rupture de médicament », pharmacie, médicament, urgence haute, photo.
2. Synchronisez.
3. Dans les 10 minutes, ou tout de suite avec *Run workflow*, le message texte, le message vocal et la photo arrivent sur Telegram.

> Au premier lancement, **tous** les signalements déjà présents sont envoyés une fois.
> Ensuite, seuls les nouveaux le sont (la liste est mémorisée dans `etat.json`).

---

### Contenu du dossier
- `notifier.py` : le script (QFieldCloud → résumé par l'IA → voix → Telegram)
- `requirements.txt` : les bibliothèques Python nécessaires
- `.github/workflows/notifier.yml` : la planification automatique toutes les 10 minutes
