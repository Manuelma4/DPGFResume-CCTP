# Contrat de préparation TCO

Endpoint actuel, en lecture :

```http
GET /api/v1/analyses/{analysis_id}/tco
```

Exemple abrégé :

```json
{
  "schema_version": "1.0",
  "source_application": "dpgf-resume-cctp",
  "analysis_id": "identifiant-stable",
  "generated_at": "2026-07-27T14:00:00Z",
  "project": {
    "name": "BONDUELLE — Bâtiment Gay Lussac",
    "reference": "25_189",
    "client": "BONDUELLE",
    "phase": "PRO",
    "due_date": ""
  },
  "lots": [
    {
      "external_id": "lot_...",
      "code": "03",
      "title": "GROS ŒUVRE",
      "source_document_id": "src_...",
      "lines": [
        {
          "external_id": "ln_...",
          "parent_level": 3,
          "kind": "item",
          "code": "3.1.1",
          "designation": "Terrassements",
          "unit": "m³",
          "quantity": null,
          "included": true,
          "source_page": 12
        }
      ]
    }
  ]
}
```

## Règles proposées pour la future écriture

- clé d'idempotence : `analysis_id` ;
- clé projet métier : `project.reference` ;
- clé lot : `external_id`, avec `code` comme information métier ;
- clé ligne : `external_id`, jamais l'intitulé seul ;
- les sections sont transmises afin de conserver la hiérarchie ;
- `included=false` signifie option ou variante hors offre de base ;
- les fichiers Excel restent servis par un endpoint REST dédié ;
- l'envoi doit être journalisé avec date, utilisateur et réponse TCO ;
- aucune connexion directe à la base TCO.

La mutation finale dépend de la validation de l'API et du modèle de données TCO.

