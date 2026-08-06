# Architecture — DPGF Résumé CCTP

## Position dans MODUO

DPGF Résumé CCTP est une application satellite. Elle possède son frontend, son
backend, son stockage et son historique. Elle utilise l'identité MODUO par OIDC et
ne lit directement aucune base ModuoCopil ou TCO.

```text
Navigateur
   │ session opaque HttpOnly
   ▼
Frontend React ── même origine ── FastAPI
                                  ├── OIDC MODUO
                                  ├── Extracteurs PDF / DOCX
                                  ├── Structuration DPGF
                                  ├── Export openpyxl
                                  ├── SQLite historique
                                  └── Fichiers sources / exports
                                              │
                                              └── futur échange API Gateway → TCO
```

## Flux de traitement

1. L'API valide les extensions, le nombre et la taille des documents.
2. Chaque fichier est stocké avec un identifiant interne et un SHA-256.
3. Le traitement est lancé en tâche de fond.
4. PyMuPDF ou python-docx extrait des blocs ordonnés.
5. Le parseur reconstruit la hiérarchie et infère uniquement les unités/quantités
   soutenues par le texte.
6. LIHA peut vérifier les lignes déterministes sans ajouter de code inconnu.
7. Les résultats sont enregistrés dans SQLite et exposés à l'interface.
8. Les corrections et objets manuels remplacent la version précédente de manière
   contrôlée.
9. L'export repart du modèle original et reconstruit les feuilles/formules.

## Sécurité

- propriétaire immuable par claim OIDC `sub` ;
- session serveur opaque, cookie `HttpOnly` ;
- PKCE, nonce et état à usage unique pour OIDC ;
- contrôle du propriétaire sur lecture, modification, source, export et suppression ;
- noms de fichiers neutralisés et extensions en liste blanche ;
- limite de taille appliquée pendant l'écriture en flux ;
- chemins d'analyse validés avant accès ;
- backend publié seulement derrière le reverse proxy en production ;
- aucun secret dans Git ou dans l'image Docker.

## Évolution prévue

- worker durable pour les dossiers volumineux ;
- OCR de PDF scannés ;
- règles métier validées par Brice ;
- mutation API Gateway pour créer ou mettre à jour un dossier dans TCO ;
- PostgreSQL et stockage objet partagé en production multi-instance.

