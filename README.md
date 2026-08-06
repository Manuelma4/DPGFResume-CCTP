# DPGF Résumé CCTP

Application satellite MODUO qui transforme un ou plusieurs CCTP en DPGF Excel
standardisés, contrôlables et traçables.

Le dossier applicatif est indépendant de ModuoCopil et de TCO :

```text
C:\Projet WEB\DPGFResume CCTP
```

## Fonctions livrées

- import multiple de CCTP PDF texte et Word `.docx` ;
- un CCTP peut devenir un lot et plusieurs CCTP un classeur multi-feuilles ;
- extraction des codes, sections, postes, unités et quantités explicites ;
- indice de confiance, raison du contrôle et extrait source par poste ;
- correction directe des lignes avant export ;
- ajout d'un objet manuel avant la génération, comme dans FTMGen ;
- choix « offre de base » ou « option / variante » pour chaque objet manuel ;
- historique persistant par utilisateur ;
- export conforme au modèle joint `DPGF TYPE.xlsx` ;
- prix unitaires déverrouillés, structure et formules protégées ;
- contrat JSON versionné pour la future connexion à TCO ;
- mode local et authentification OIDC/SSO MODUO pour la production.

## Installation locale

Prérequis : Python 3.11+, Node.js 18+.

```powershell
cd "C:\Projet WEB\DPGFResume CCTP"
.\setup.ps1
.\run.ps1
```

Ouvrir ensuite :

```text
http://127.0.0.1:8070
```

Pour relancer en fermant d'abord une ancienne instance du même port :

```powershell
.\run.ps1 -Restart
```

## Parcours utilisateur

1. Ouvrir « Nouveau dossier ».
2. Renseigner projet, référence, client, phase et échéance.
3. Déposer un ou plusieurs CCTP PDF/DOCX.
4. Attendre la fin de l'extraction.
5. Contrôler les lignes orange et consulter leur extrait source.
6. Corriger les codes, désignations, unités ou quantités si nécessaire.
7. Utiliser « Ajouter un objet » pour créer un poste absent du CCTP.
8. Enregistrer puis « Générer l'Excel ».

L'objet manuel est conservé dans l'historique. S'il est déclaré option/variante,
sa ligne est exportée mais n'entre pas dans le total de l'offre de base.

## Excel généré

Le fichier reprend la présentation du modèle fourni :

- en-tête projet, phase, date et entreprise ;
- colonnes Code, Désignation, Unité, Quantité, P.U. HT et Montant HT ;
- hiérarchie et sous-totaux par section principale ;
- total HT hors prorata ;
- prorata 1,5 % ;
- total HT compris prorata ;
- TVA 20 % ;
- total TTC ;
- notice de remplissage.

Les formules TVA/TTC ont été corrigées par rapport aux incohérences présentes dans
le fichier source. Les cellules de prix unitaire sont jaunes et déverrouillées.

Le modèle original embarqué reste téléchargeable depuis l'application et se trouve
dans `app/assets/DPGF TYPE.xlsx`.

## Historique et stockage

```text
output/
  dpgf_resume.sqlite3
  auth.sqlite3
  analyses/<id>/
    sources/
    exports/
```

Les requêtes filtrent chaque dossier avec le claim OIDC stable `sub`. Une application
n'accède ni à la base ni aux fichiers d'une autre application MODUO.

## Authentification MODUO

Le mode local utilise un utilisateur de développement. La production échoue de
manière fermée si OIDC est exigé mais incomplet.

Créer `.env` à partir de `.env.example`, puis renseigner :

```env
DPGF_ENVIRONMENT=production
DPGF_AUTH_REQUIRED=true
DPGF_PUBLIC_URL=https://dpgf.moduo.fr
DPGF_SESSION_SECRET=...
OIDC_ISSUER_URL=https://auth.moduo.fr/application/o/dpgf-resume-cctp
OIDC_CLIENT_ID=dpgf-resume-cctp
OIDC_CLIENT_SECRET=...
OIDC_REDIRECT_URI=https://dpgf.moduo.fr/api/auth/callback
OIDC_POST_LOGOUT_REDIRECT_URI=https://dpgf.moduo.fr/
OIDC_REQUIRED_GROUP=Moduo Access - DPGF Resume CCTP
```

Le navigateur ne reçoit qu'une session opaque `HttpOnly`, `Secure` et `SameSite=Lax`.
Les mots de passe restent dans le fournisseur d'identité MODUO.
Le jeton doit également contenir exactement un groupe de rôle
`Moduo Role - Admin`, `Moduo Role - Copil` ou
`Moduo Role - Collaborateur`. Le rôle métier et l'accès à DPGF sont donc
contrôlés séparément.

## Assistance LIHA facultative

L'extraction déterministe fonctionne sans service externe. LIHA peut vérifier les
lignes existantes, mais les suggestions sans correspondance avec la structure
détectée sont ignorées afin de limiter les hallucinations.

```env
DPGF_USE_LLM=true
LIHA_CHAT_COMPLETIONS_URL=...
LIHA_CHAT_MODEL=...
LIHA_CHAT_TOKEN=...
```

Chaque résultat indique si LIHA a réellement été utilisé.

## Préparation TCO

Le contrat de lecture est disponible ici :

```http
GET /api/v1/analyses/{analysis_id}/tco
```

Il expose `schema_version`, projet, lots, identifiants stables, codes, unités,
quantités et références de source. L'application ne pousse encore rien vers TCO :
la mutation d'envoi et les règles de correspondance seront ajoutées lorsque son API
sera validée.

Voir [docs/TCO-CONTRACT.md](docs/TCO-CONTRACT.md).

## Déploiement pilote

DPGF Résumé CCTP est le premier pilote Moduo Connect. Cette installation est
indépendante de TCO et de ModuoCopil : elle utilise le domaine
`dpgf.moduo.fr`, le port local `127.0.0.1:8070` et son propre volume Docker.

Les instructions WinSCP, Docker, Apache et TLS se trouvent dans
[`deploy/README.md`](deploy/README.md).

## Validation

```powershell
.\validate.ps1
```

La validation compile le backend, exécute les tests extraction/API/Excel et compile
le frontend React.

## Limites connues

- les fichiers Word historiques `.doc` doivent être convertis en `.docx` ;
- les PDF scannés sans couche texte sont signalés et nécessiteront un service OCR ;
- une quantité qui n'est pas explicitement écrite dans le CCTP reste vide ;
- l'utilisateur reste responsable de la validation économique avant diffusion.
