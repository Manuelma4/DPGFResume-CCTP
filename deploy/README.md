# Pilote DPGF Resume CCTP sur 51.38.56.20

Ce pilote ne modifie ni `/home/anetmo/tco` ni
`/var/www/moduocopil/ModuoCopil`.

## 1. DNS

Créer dans N0C :

```text
A  14400  dpgf.moduo.fr.  51.38.56.20
```

`14400` est correct lorsque l'adresse est déjà validée. Un TTL de `60` est
seulement utile avant une modification DNS imminente.

## 2. Transfert WinSCP

Transférer le dossier applicatif dans :

```text
/home/mathis/dpgf-resume-cctp
```

Ne pas transférer `.git`, `.venv`, `node_modules`, `__pycache__`, `.env`,
`output`, fichiers `*.pyc` ou journaux. Le fichier `.env` est créé uniquement
sur le serveur.

## 3. Configuration

Dans le terminal SSH :

```bash
cd /home/mathis/dpgf-resume-cctp
cp .env.example .env
chmod 600 .env
openssl rand -base64 48
```

Mettre le résultat dans `DPGF_SESSION_SECRET`, puis renseigner dans `.env` le
secret du client Authentik `dpgf-resume-cctp`. Ne pas réutiliser un secret
d'une autre application.

Le client Authentik doit utiliser exactement :

```text
Issuer:   https://auth.moduo.fr/application/o/dpgf-resume-cctp
Callback: https://dpgf.moduo.fr/api/auth/callback
Logout:   https://dpgf.moduo.fr/
Groupe:   Moduo Access - DPGF Resume CCTP
```

## 4. Démarrage privé

```bash
docker compose --env-file .env config --quiet
docker compose build --pull
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8070/api/health
```

Le port attendu est uniquement `127.0.0.1:8070`. Ne pas ouvrir le port 8070
dans le pare-feu.

## 5. Apache et TLS

```bash
sudo a2enmod proxy proxy_http headers rewrite ssl
sudo cp apache/dpgf.conf /etc/apache2/sites-available/dpgf.conf
sudo a2ensite dpgf.conf
sudo apachectl configtest
sudo systemctl reload apache2

sudo certbot certonly --apache -d dpgf.moduo.fr

sudo cp apache/dpgf-le-ssl.conf.example \
  /etc/apache2/sites-available/dpgf-le-ssl.conf
sudo a2ensite dpgf-le-ssl.conf
sudo apachectl configtest
sudo systemctl reload apache2
```

Vérifier :

```bash
curl --fail https://dpgf.moduo.fr/api/health
```

## 6. Validation pilote

Avant le premier essai, ajouter uniquement l'utilisateur pilote aux groupes
`Moduo Users`, `Moduo Role - <son rôle ModuoCopil>` et
`Moduo Access - DPGF Resume CCTP`.

1. un utilisateur sans le groupe DPGF est refusé ;
2. connexion et déconnexion via Moduo Connect ;
3. `/api/auth/me` retourne le rôle Moduo attendu ;
4. import PDF et DOCX ;
5. génération et téléchargement Excel ;
6. persistance après redémarrage du conteneur ;
7. utilisateur A incapable de voir l'historique de l'utilisateur B ;
8. absence de secrets et de jetons dans les journaux.

TCO et ModuoCopil restent inchangés pendant toute cette validation.
