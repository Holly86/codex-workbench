# Synology-Deployment

Dieses Runbook gilt ausschließlich für die reale Synology DS918+ unter
`/volume1/docker/codex`. Alle Befehle werden aus
`/volume1/docker/codex/workspace/repositories/codex-workbench` ausgeführt. Die Beispiele verwenden
das auf DSM vorhandene Kommando `docker-compose`, nicht das neuere `docker compose`.
Falls der DSM-Benutzer keinen direkten Zugriff auf den Docker-Daemon hat, werden die gezeigten
Docker-/Compose-Befehle mit `sudo` ausgeführt.

## Zielarchitektur

Compose baut zwei linux/amd64-Images und startet genau je einen Container:

- `frontend` ist ein unprivilegierter Nginx und der einzige Service mit einem veröffentlichten
  Host-Port. Er liefert die Weboberfläche aus und leitet `/api` und `/ws` in das private Netz weiter.
- `backend` ist FastAPI mit genau einem Uvicorn-Worker. Es startet Codex CLI 0.146.0 als direktes
  Kind `codex app-server --stdio`; der App-Server öffnet und exponiert keinen Netzwerkport.
- `workbench-private` verbindet nur Frontend und Backend und erlaubt dem Backend ausgehende
  Verbindungen. Es ist absichtlich kein `internal`-Netz, weil Codex den OpenAI-Dienst erreichen muss.
- Das Frontend hängt zusätzlich am bereits vorhandenen externen Netzwerk `backend-net`. Compose
  verwaltet dessen Subnetz und IPAM nicht und setzt weder `ipv4_address` noch eine andere feste
  Container-IP. Das Netz verleiht der Workbench insbesondere keine eigene LAN-IP.
- Die einzige Host-Portfreigabe ist unveränderlich `127.0.0.1:8085:8080` am Frontend. Sie ist weder
  über `0.0.0.0` noch über eine NAS-LAN-IP erreichbar. Regulärer Browserzugang erfolgt ausschließlich
  über den HTTPS Reverse Proxy der Synology; direkte LAN-Freigabe, DSM-Portweiterleitung und
  Router-NAT für 8085 sind unzulässig.

Der bestehende SSH-Codex-Container wird weder ersetzt noch von diesem Compose-Projekt verwaltet.

Die bereits belegten Hostports `1880`, `3000`, `3002`, `5005`, `5678`, `8010`, `8080`, `8081`,
`8890`, `11434`, `1883` und `1884` werden nicht verwendet oder verändert. Der Installer akzeptiert
nur 8085 auf Loopback und bricht ab, wenn dieser Port bereits lauscht.

## Persistente Hostpfade

Es gibt keine benannten oder anonymen Docker-Volumes. Jeder dauerhafte Schreibpfad ist ein absoluter
Bind-Mount unter `/volume1/docker/codex`.

| Hostpfad | Containerpfad | Zugriff | Inhalt |
| --- | --- | --- | --- |
| `/volume1/docker/codex/workbench/data` | `/var/lib/codex-workbench` | rw | Workbench-Metadatenroot |
| `/volume1/docker/codex/workbench/data/state` | wie oben, Unterpfad | rw | SQLite einschließlich WAL/SHM |
| `/volume1/docker/codex/workbench/uploads` | `/var/lib/codex-workbench/uploads` | rw | temporäre private Uploaddateien |
| `/volume1/docker/codex/workbench/secrets/password` | `/var/lib/codex-workbench/security` | rw | exklusiv erzeugter Argon2id-Verifier |
| `/volume1/docker/codex/workbench/secrets/session` | `/run/secrets/workbench` | ro | Sitzungsschlüssel `session.key` |
| `/volume1/docker/codex/config/.codex` | `/home/workbench/.codex` | rw | bestehende Codex-Anmeldung, Konfiguration und Codex-Threads |
| `/volume1/docker/codex/workspace` | `/workspace` | rw | Workspace und Repositories |
| Repository-`.env` | nur Compose auf dem Host | nicht gemountet | Workbench-Konfiguration ohne Credentials |

Der Installer legt keine vierte Workbench-Persistenz und kein Backupverzeichnis an. Backups gehen in
ein vom Betreiber ausdrücklich gewähltes, extern gesichertes Ziel und sind keine Runtime-Mounts. Ein
eigener Log-Mount ist nicht erforderlich: beide Services schreiben nach stdout/stderr; der
Docker-JSON-Logger rotiert pro Service bei 10 MiB und behält drei Dateien.

Der Image-Root ist read-only. Schreibbar sind nur die genannten Backend-Bind-Mounts und begrenzte
tmpfs-Pfade für `/tmp` sowie ephemere Benutzer-Caches. Das Frontend erhält ausschließlich tmpfs für
Nginx-Laufzeitpfade und überhaupt keine Hostdaten.

## Identitäts- und Rechtemodell

Build und Compose verwenden verbindlich UID `1026` und GID `100`. Der Installer lehnt andere Werte
und abweichende Eigentümer von Codex-Home oder Workspace ab, ändert diese gemeinsamen Pfade aber
nicht. Beide Images werden mit derselben numerischen Identität gebaut und laufen vollständig
non-root.

Workbench-Verzeichnisse sind Modus 0700, Sitzungsschlüssel, Passwort-Verifier, SQLite-Dateien und
Uploads Modus 0600. Synology-ACLs können POSIX-Modi erweitern; sie müssen zusätzlich in DSM File
Station oder mit den auf dem NAS verfügbaren ACL-Werkzeugen geprüft werden. Der Runtime-Test in
[synology-test-plan.md](synology-test-plan.md) ist deshalb verpflichtend.

Beide Container laufen ohne `privileged`, ohne Docker-Socket, ohne Geräte, ohne Host-PID/IPC/Netz,
mit `no-new-privileges`, sämtlichen gedroppten Linux-Capabilities, PID-/Speicher-/nofile-Limits und
read-only Root-Filesystem. Full Access hebt die Codex-Sandbox auf, nicht diese Containergrenzen.

Der DS918+-Kernel unterstützt keine Docker-CPU-CFS-Quotas. Deshalb enthält Compose bewusst weder
`cpus` noch `cpu_period`/`cpu_quota` oder gleichartige CFS-Grenzen; die frühere DSM-Warnung entfällt.
CPU-Isolation kann auf diesem Host nicht durch Compose erzwungen werden. Ein Uvicorn-Worker, ein
verwalteter App Server, begrenzte Queues/Uploads, 2 GiB Backend-RAM, 128 MiB Frontend-RAM, begrenzte
tmpfs-Pfade und keine automatischen Last-/Soak-Tests reduzieren stattdessen die Konkurrenz zu den
bestehenden Diensten.

Nur der Backend-Service setzt `init: true`: das Python-Slim-Image besitzt kein eigenes Init-System,
und Docker Init übernimmt das Reaping weiterer Kindprozesse neben dem expliziten Supervisor. Das
Frontend verwendet das unveränderte unprivilegierte Nginx-Image ohne zusätzlichen Init. Keines der
Images verwendet s6-overlay; der bekannte s6/PID-1-Konflikt kann hier daher nicht auftreten.

## Voraussetzung: geteiltes Codex-Home

Das bestehende `/volume1/docker/codex/config/.codex` wird absichtlich mit dem SSH-Codex-Container
geteilt, damit Anmeldung und Threads erhalten bleiben. Zwei voneinander unabhängige Codex-Prozesse
können jedoch gleichzeitig Token-Refresh, Konfiguration, Threaddateien oder interne Indizes ändern;
Workbench implementiert dafür keine prozessübergreifende Sperre.

Vor Installation, Backup, Restore, Update und einem Workbench-Turn müssen aktive Codex-CLI-Turns im
SSH-Container beendet sein. Der SSH-Container selbst muss nicht ersetzt oder neu gestartet werden.
Für echten Parallelbetrieb ist ein separat authentifiziertes Codex-Home die sichere Alternative;
ein bloß kopiertes Home kann auslaufende Refresh-Tokens und divergierende Threads erzeugen und ist
kein belastbares Synchronisationsverfahren.

## Initialinstallation

1. Als DSM-Administrator per SSH anmelden und den Zielzustand prüfen:

       cd /volume1/docker/codex/workspace/repositories/codex-workbench
       pwd -P
       git status --short --branch
       git rev-parse HEAD
       uname -m
       docker version
       docker-compose version
       docker network inspect backend-net

   `uname -m` und Docker-Architektur müssen `x86_64`/`amd64` entsprechen. Das externe Netzwerk muss
   existieren. Das Repository darf nicht durch das Deploymentskript aktualisiert oder bereinigt
   werden.

2. Sicherstellen, dass keine aktive SSH-Codex-Sitzung gerade in das gemeinsame Codex-Home schreibt.
   Vorhandene Authentifizierungsdateien nicht kopieren, umbenennen oder löschen.

3. Installer erstmals als root ausführen:

       sudo ./scripts/init-deployment.sh

   Fehlt `.env`, wird sie aus `.env.example` angelegt. Der erste Lauf legt Workbench-Verzeichnisse
   und – nur wenn nicht vorhanden – einen Sitzungsschlüssel an. Anschließend endet er absichtlich
   mit einem Fehler, solange Dokumentationsadressen oder der Image-Tag-Platzhalter enthalten sind.
   Kein Container wird gebaut oder gestartet.

4. `.env` restriktiv bearbeiten. Mindestens diese Werte setzen:

       WORKBENCH_IMAGE_TAG=<eindeutige-release-id>
       WORKBENCH_PUBLIC_ORIGIN=https://<exakter-reverse-proxy-hostname>
       WORKBENCH_SESSION_COOKIE_SECURE=true
       WORKBENCH_ALLOW_INSECURE_HTTP=false
       PUID=1026
       PGID=100

   `WORKBENCH_PUBLIC_ORIGIN` muss exakt dem später im Browser sichtbaren HTTPS-Origin entsprechen,
   einschließlich eines nicht standardmäßigen öffentlichen Ports, aber ohne Pfad oder abschließenden
   Slash. Ein reservierter Beispieldomainname wie `.invalid`, `.example.com` oder eine TEST-NET-IP
   wird vom Installer als Dokumentationsplatzhalter abgewiesen.

   Nur für einen host-lokalen Test ohne Reverse Proxy ist vorübergehend exakt Folgendes zulässig:

       WORKBENCH_PUBLIC_ORIGIN=http://127.0.0.1:8085
       WORKBENCH_SESSION_COOKIE_SECURE=false
       WORKBENCH_ALLOW_INSECURE_HTTP=true

   Dieser HTTP-Modus ist kein regulärer LAN-Betrieb und darf ausschließlich auf dem NAS selbst
   verwendet werden. Passwörter, Codex-Tokens und Sitzungsschlüssel gehören nicht in `.env`.
   `WORKBENCH_IMAGE_TAG` ist pro Release eindeutig, zum Beispiel eine freigegebene Commit-ID;
   dadurch bleiben vorige Images rollbackfähig. Bind-Adresse und Host-Port sind nicht konfigurierbar
   und deshalb keine `.env`-Variablen.

5. Installer erneut ausführen:

       sudo ./scripts/init-deployment.sh

   Er normalisiert eine CRLF-`.env` atomar auf LF, ohne sie als Shellcode auszuführen, prüft alle
   Shellskripte und `.env.example` auf LF, validiert numerisch exakt `1026:100`, alle sicherheits-
   und ressourcenrelevanten Werte, die exakten Hostpfade und Eigentümer sowie den Schema-/CLI-Pin.
   `backend-net` muss vorhanden und ein Bridge-Netz sein; TCP 8085 muss frei sein. Das vollständig
   gerenderte Compose-Modell darf nur die Images des gewählten Tags, `linux/amd64`, die zwei
   vorgesehenen Services/Netze und exakt `127.0.0.1:8085:8080` enthalten. Zusätzliche Ports,
   `docker-net`, macvlan, feste IPs, `container_name`, Host-Netz und CPU-CFS-Schlüssel führen vor
   Build/Start zum Abbruch. Bestehende Daten, Secrets, Codex-Authentifizierung und Repositories
   werden nie gelöscht oder inhaltlich überschrieben. Da der Port frei sein muss, wird der
   Installer nur bei gestoppter Workbench ausgeführt.

6. Den Reverse Proxy wie im folgenden Abschnitt konfigurieren und testen.

## Synology Reverse Proxy

In DSM unter „Anmeldeportal/Reverse Proxy“ wird eine HTTPS-Regel mit einem gültigen Zertifikat
angelegt. Verbindliche Werte und Eigenschaften:

- Quelle: der Hostname und öffentliche Port aus `WORKBENCH_PUBLIC_ORIGIN`, Protokoll HTTPS.
- Ziel: `http://127.0.0.1:8085` ohne zusätzlichen Pfad.
- Der ursprüngliche `Host`-Header wird einschließlich eines gegebenenfalls nicht standardmäßigen
  Ports weitergereicht; er darf nicht auf `127.0.0.1:8085` umgeschrieben werden.
- `X-Forwarded-Proto` beziehungsweise Forwarded-Proto wird am Edge auf `https` gesetzt. Das
  Workbench-Nginx reicht nur die Werte `http` oder `https` an das Backend weiter.
- WebSocket-Weiterleitung ist aktiviert: HTTP/1.1 sowie `Upgrade` und `Connection` bleiben erhalten.
  `/ws` und `/api` werden weder gecacht noch gepuffert; lange WebSocket-Verbindungen erhalten einen
  ausreichend großen Timeout.
- Der Browser-Header `Origin` wird nicht ersetzt. Sein Wert muss exakt `WORKBENCH_PUBLIC_ORIGIN`
  sein. Quellregel, Zertifikat, Host-Header und Origin verwenden daher denselben Hostnamen und Port.
- Auf DSM-Firewall, Router und Compose gibt es keine direkte LAN-Freigabe für TCP 8085. Insbesondere
  wird kein Port-Forwarding auf 8085 und keine alternative HTTP-Regel angelegt.

Nach erfolgreicher Prüfung kann HSTS am HTTPS-Edge aktiviert werden. Die Frontend-Konfiguration in
`deploy/nginx.conf` erhält Host, Forwarded-Proto, Clientkette und WebSocket-Header auf dem internen
Hop; sie macht den Loopback-Port nicht selbst extern erreichbar. Im Production-Modus prüft das
Backend für jede Nicht-Health-API und jeden WebSocket zusätzlich genau einen passenden `Host`,
`X-Forwarded-Host` und `X-Forwarded-Proto`. Eine Proxy-Regel, die Host oder HTTPS-Schema nicht wie
oben beschrieben erhält, scheitert daher sichtbar und fail-closed.

## Konfiguration, Build und Start

Vor Initialinstallation oder einem Start nach Wartungsarbeiten muss TCP 8085 frei sein:

    cd /volume1/docker/codex/workspace/repositories/codex-workbench
    sudo ./scripts/init-deployment.sh
    sudo docker-compose config

Build mit den in `.env` gepinnten Versionen:

    sudo docker-compose build --pull --no-cache

Der Backend-Build installiert exakt `@openai/codex@0.146.0`, verlangt bereits in Build- und
Runtime-Stage exakt `codex-cli 0.146.0` und prüft `codex app-server --help`. Der Backend-Start
verweigert Readiness, wenn die Laufzeitversion nicht exakt zum generierten 0.146.0-Schema passt.
Node 24.18.1 und Python 3.12.11 werden vom Installer ebenfalls festgehalten.

Die Runtime übernimmt die vollständige globale `node_modules`-Struktur aus der Node-Stage und legt
die npm-Bin-Symlinks darin explizit neu an. Dadurch bleibt der Codex-Launcher am von npm erwarteten
Ort relativ zu `@openai/codex` und findet das native Paket `@openai/codex-linux-x64` samt seiner
linux/amd64-Artefakte. `libstdc++6` wird als explizite Node-Runtime-Abhängigkeit installiert. Ein
separater, secrets-freier Build-Check meldet fehlende Launcher, Pakete, native Dateien oder
Libraries, Exitcodes und unerwartete stdout/stderr-Kanäle getrennt; Version und `--stdio`-Hilfe
bleiben fail-closed geprüft.

Buildx kann auf DSM melden, dass Host-Git fehlt und Commitinformationen nicht erfasst wurden. Das
ist harmlos und kein Grund für eine Git-Installation auf DSM: `.git` liegt nicht im Buildkontext,
es gibt kein Commit-Label und keine erfundene Provenance, und die Anwendung benötigt zur Laufzeit
keine Build-Commitmetadaten. Der Buildinhalt wird durch Quellstand, Lockfiles und gepinnte Versionen
bestimmt; ein Release-Tag in `.env` bleibt die explizite Betreiberzuordnung.

Vor dem ersten Start die Images prüfen:

    workbench_tag=$(sed -n 's/^WORKBENCH_IMAGE_TAG=//p' .env)
    docker image inspect "codex-workbench-backend:${workbench_tag}"
    docker image inspect "codex-workbench-frontend:${workbench_tag}"

Start ohne impliziten Neubau:

    sudo docker-compose up -d
    sudo docker-compose ps

Nur `frontend` darf exakt `127.0.0.1:8085->8080/tcp` als Host-Portzuordnung besitzen. `backend` darf
in `docker-compose ps` keinen veröffentlichten Port zeigen. Insbesondere gibt es keinen Port für den
als stdio-Kind laufenden Codex App Server.

## Logs und Healthchecks

    docker-compose logs --tail=200 frontend backend
    docker-compose logs --tail=200 -f backend
    curl --fail --silent --show-error http://127.0.0.1:8085/healthz
    curl --fail --silent --show-error http://127.0.0.1:8085/api/health/live
    curl --fail --silent --show-error http://127.0.0.1:8085/api/health/ready

Diese HTTP-Aufrufe werden ausschließlich in einer Shell auf der Synology ausgeführt. Für den
regulären Test werden dieselben Pfade zusätzlich über den kanonischen HTTPS-Origin aufgerufen; ein
Zugriff auf `http://<NAS-LAN-IP>:8085` muss scheitern. `/healthz` prüft Nginx. `live` prüft den
FastAPI-Prozess. `ready` ist erst 200, wenn Workspace, Repositoryroot, Workbench-Daten,
SQLite/Security und die initialisierte App-Server-Generation bereit sind. Ein noch fehlender
Passwort-Verifier ist kein Readiness-Fehler.

Der normale laufende Zustand enthält unter anderem `application_started` und `app_server_ready`,
aber keine Prompts, Uploadinhalte, Cookies, Token, komplette Hostpfade oder rohen App-Server-Stderr.
Nginx-Access-Logs sind deaktiviert.

Der Backend-Healthcheck wartet auf der langsameren DS918+ zunächst 60 Sekunden, prüft danach alle
30 Sekunden mit 5 Sekunden Timeout und fünf Retries. Das Frontend wartet über `service_healthy` und
startet daher entweder nach echter Readiness oder bleibt mit einem klaren Backendfehler aus; es gibt
weder eine Abschwächung auf `/live` noch eine endlose aggressive Startschleife. Ein späterer
App-Server-Restart macht `/ready` vorübergehend 503 und nach erfolgreicher neuer Initialisierung
wieder 200.

Der Compatibility-Probe startet den validierten `/usr/local/bin/codex`-Launcher direkt mit dem
festen Argument `--version`, nie über eine Shell. Er akzeptiert nach äußerem Whitespace genau eine
UTF-8-Zeile `codex-cli 0.146.0`; LF und CRLF sind zulässig. `0.146.0`, `codex 0.146.0`, zusätzliche
stdout-Zeilen, andere Versionen, nicht-leeres stderr oder Exitcodes ungleich null werden nicht
erraten oder als Teilstring akzeptiert. Die sichere Logursache ist genau eine von
`binary_not_found`, `execution_failed`, `malformed_version_output`, `unsupported_version`,
`timeout`, `permission_denied` oder `unexpected_stderr`. Weder stdout/stderr-Inhalt noch
Umgebungswerte werden geloggt. Eine bekannte harmlose CLI-Warnung wird somit nicht als Versionswert
interpretiert; bei `unexpected_stderr` sind insbesondere UID/GID, HOME/CODEX_HOME und tmpfs-Rechte
mit dem Diagnosewerkzeug zu prüfen.

Nach dem Start liefert die folgende rein lesende Diagnose ausschließlich Metadaten der beiden
Workbench-Services. Sie zeigt keine Authdateien, Cookies, Session-/CSRF-Werte oder Dateiinhalte und
verändert keine Container:

    cd /volume1/docker/codex/workspace/repositories/codex-workbench
    sudo ./scripts/diagnose-deployment.sh

Sie prüft `command -v codex`, Version und App-Server-Hilfe im Backend, UID/GID, HOME/CODEX_HOME,
Mountzugriff, Live/Ready, den einen App-Server-Prozess, effektive Ports/Netze und die
Workbench-eigenen Containerzustände.

## Schutz der bestehenden Synology-Dienste

Alle Compose-Befehle werden ausschließlich in diesem Repository mit
`COMPOSE_PROJECT_NAME=codex-workbench` ausgeführt. Es gibt keine expliziten `container_name`-Werte;
Compose erzeugt kollisionsarme projektspezifische Namen. Verboten sind `docker system prune`,
globales `docker image prune`, `docker network prune`, `docker volume prune`, das Entfernen von
`backend-net`, Stop/Recreate fremder Container und das Ändern bestehender Reverse-Proxy-Ziele oder
belegter Ports. Insbesondere werden Daten von Alivo, Nextcloud, Paperless, n8n, ioBroker, Ollama,
Pool Community, Signage und Homebridge sowie `/volume1/docker/alivo` niemals berührt.

`docker-compose down` darf nur aus diesem Projektverzeichnis und nur für den Workbench-Stack
verwendet werden; `down -v` ist unzulässig. Eine risikoarme Bereinigung entfernt ausschließlich
zuvor geprüfte, unbenutzte Workbench-Images anhand ihres vollständigen Tags:

    sudo docker image rm codex-workbench-backend:<alter-tag>
    sudo docker image rm codex-workbench-frontend:<alter-tag>

Vorher muss `docker-compose config --images` bestätigen, dass der Tag nicht mehr aktiv ist. Es gibt
keinen generischen Prune-Befehl.

## Erster Bootstrap und Login

Die öffentlich erreichbare Setup-Route ist beim ersten Start offen. Der erste Besucher am exakten
Origin kann die Einzelbenutzer-Instanz übernehmen. Deshalb:

1. Die HTTPS-Reverse-Proxy-Regel zunächst nur für den Administrator-Testkreis erreichbar machen;
   TCP 8085 bleibt unabhängig davon ausschließlich an Host-Loopback gebunden.
2. Den exakten `WORKBENCH_PUBLIC_ORIGIN` öffnen. Keine alternative IP, keinen Hostalias und keinen
   abweichenden Port verwenden.
3. In „Ersteinrichtung“ ein einmaliges starkes Passwort mit mindestens 12 Zeichen setzen. Dadurch
   entsteht exklusiv `secrets/password/password.argon2id`; es gibt kein Default-Passwort.
4. Abmelden und mit demselben Passwort erneut anmelden. Setup darf danach nicht wieder angeboten
   werden.
5. Erst danach den vorgesehenen Nutzerkreis am HTTPS Reverse Proxy freigeben. TCP 8085 wird dabei
   weder an eine LAN-IP gebunden noch per Firewall oder NAT direkt freigegeben.

Eine Passwortänderungs- oder Recovery-Funktion existiert noch nicht. Den Verifier niemals als
„Reset“ auf einer laufenden Instanz löschen; dies würde das Setup-Fenster erneut öffnen.

## Erster Workbench-Ablauf

1. In der linken Navigation ein direktes Git-Repository unter
   `/volume1/docker/codex/workspace/repositories` auswählen. Verschachtelte oder symlinkbasierte
   Repositories werden absichtlich nicht angeboten.
2. Einen neuen Thread zunächst mit `Workspace write` und `On request` öffnen.
3. Im Composer ein PNG/JPEG/WebP/GIF aus der Zwischenablage einfügen. Uploadfortschritt und danach
   „Bereit“ müssen erscheinen. Eine entfernte Anlage wird sofort gelöscht; übrige Dateien laufen
   nach `WORKBENCH_UPLOAD_TTL_SECONDS` ab und werden beim Start beziehungsweise nächsten Upload
   bereinigt. Metadaten bleiben als Lifecycle-Audit in SQLite.
4. Eine harmlose Aufgabe mit Bild senden. „Working…“, Assistant-Deltas, Aktivität und terminaler
   Zustand müssen live erscheinen.
5. „Nutzung & Kontingent“ öffnen und Kontext-, Token- und Rate-Limit-Anzeige gegen den angemeldeten
   Codex-Account plausibilisieren. Ein reales Kontingent soll nicht absichtlich aufgebraucht werden.
6. Full Access und Approval-Verhalten ausschließlich im disposable Testrepository nach
   [synology-test-plan.md](synology-test-plan.md) prüfen.

## Graceful Shutdown

Keine neuen Turns starten und einen laufenden Turn entweder abschließen lassen oder über „Stop“
unterbrechen. Danach:

    docker-compose stop -t 30

SIGTERM erreicht Uvicorn; die Anwendung beendet neue Arbeit, schließt oder verweigert ausstehende
Approvals, schließt app-server-stdio und terminiert bei Bedarf dessen Prozessgruppe. Compose wartet
30 Sekunden auf das Backend. `docker-compose kill` ist kein regulärer Shutdown.

## Update

1. Release-ID, Commit und aktuellen Healthzustand notieren:

       git rev-parse HEAD
       sed -n 's/^WORKBENCH_IMAGE_TAG=//p' .env
       docker-compose ps

2. Konsistentes Backup wie unten beschrieben erstellen. Während des Codex-Home-Backups darf kein
   Codex-Prozess in SSH-Container oder Workbench schreiben.
3. Den freigegebenen Quellstand mit dem vom Betreiber gewählten Git-Verfahren bereitstellen. Keine
   generierten Schemas manuell ändern.
4. In `.env` eine neue eindeutige `WORKBENCH_IMAGE_TAG` setzen.
5. Laufende Turns beenden und die alte Workbench regulär stoppen. Der Installer muss den Host-Port
   8085 frei vorfinden. Danach prüfen und bauen:

       docker-compose stop -t 30
       sudo ./scripts/init-deployment.sh
       docker-compose config --quiet
       docker-compose build --pull --no-cache

6. Auf die neuen Images wechseln:

       docker-compose up -d --no-build --remove-orphans
       docker-compose ps

7. Health, Login, Repositoryliste, einen Read-only-Turn, WebSocket und Logs prüfen. Das vorige Image
   bis nach vollständiger Abnahme nicht löschen.

## Rollback

Der schnelle Rollback ist nur zulässig, wenn die Release Notes die SQLite-Migration als rückwärts
kompatibel ausweisen. Dann den vorherigen Compose-/Quellstand bereitstellen, in `.env` den alten
Image-Tag setzen und ausführen:

    docker-compose stop -t 30
    sudo ./scripts/init-deployment.sh
    docker-compose config --quiet
    docker-compose up -d --no-build --remove-orphans

Bei nicht rückwärtskompatibler oder unbekannter Migration zuerst stoppen und das koordinierte
Backup von Workbench-Daten, Uploads, Secrets und Codex-Home restaurieren. Danach alten Quellstand und
alten Image-Tag verwenden. Ein App-Rollback überschreibt niemals den allgemeinen Workspace.

## Backup

Ein konsistentes Backup braucht einen ruhenden Workbench-Backendprozess und ein ruhendes geteiltes
Codex-Home. Vorher aktive SSH-Codex-Turns beenden. Dann als root:

    cd /volume1/docker/codex/workspace/repositories/codex-workbench
    docker-compose stop -t 30
    workbench_backup_root=<vorhandenes-extern-gesichertes-backupziel>
    test -d "$workbench_backup_root" && test -w "$workbench_backup_root"
    workbench_stamp=$(date -u +%Y%m%dT%H%M%SZ)
    workbench_backup=${workbench_backup_root}/codex-workbench-control-${workbench_stamp}.tar.gz
    tar -C /volume1/docker/codex -czpf "$workbench_backup" \
      workbench/data \
      workbench/uploads \
      workbench/secrets \
      config/.codex \
      workspace/repositories/codex-workbench/.env
    sha256sum "$workbench_backup" > "${workbench_backup}.sha256"
    git rev-parse HEAD > "${workbench_backup_root}/codex-workbench-source-${workbench_stamp}.txt"
    sed -n 's/^WORKBENCH_IMAGE_TAG=//p' .env > "${workbench_backup_root}/codex-workbench-image-${workbench_stamp}.txt"
    docker-compose up -d --no-build

Das Archiv enthält SQLite/WAL, Uploaddateien und -metadaten, Passwort-Verifier, Sitzungsschlüssel,
Codex-Authentifizierung/-Threads und `.env` als eine Einheit. Es enthält absichtlich nicht alle
Workspace-Repositories. Diese werden separat mit Synology Hyper Backup beziehungsweise einem
Btrfs-Snapshot gesichert; alternativ kann im selben ruhenden Fenster ein eigenes Archiv von
`/volume1/docker/codex/workspace` erstellt werden. Das Ziel wird bewusst nicht vom Installer erzeugt
oder gemountet und darf nicht innerhalb von `workbench/data`, `workbench/uploads` oder
`workbench/secrets` liegen. Es muss auf einen anderen Datenträger repliziert und testweise
wiederhergestellt werden.

## Restore

Restore ist ein Wartungsfenster. Backup-Hash zuerst prüfen, Workbench stoppen und aktive
SSH-Codex-Prozesse beenden:

    cd /volume1/docker/codex/workspace/repositories/codex-workbench
    workbench_backup=<absoluter-pfad-zum-geprüften-control-archiv>.tar.gz
    sha256sum -c "${workbench_backup}.sha256"
    docker-compose stop -t 30
    restore_stamp=$(date -u +%Y%m%dT%H%M%SZ)
    mv /volume1/docker/codex/workbench/data "/volume1/docker/codex/workbench/data.pre-restore-${restore_stamp}"
    mv /volume1/docker/codex/workbench/uploads "/volume1/docker/codex/workbench/uploads.pre-restore-${restore_stamp}"
    mv /volume1/docker/codex/workbench/secrets "/volume1/docker/codex/workbench/secrets.pre-restore-${restore_stamp}"
    mv /volume1/docker/codex/config/.codex "/volume1/docker/codex/config/.codex.pre-restore-${restore_stamp}"
    cp -p .env "/volume1/docker/codex/workbench/config.pre-restore-${restore_stamp}.env"
    tar -C /volume1/docker/codex -xzpf "$workbench_backup"
    sudo ./scripts/init-deployment.sh
    docker-compose config --quiet
    docker-compose up -d --no-build

Die `pre-restore`-Pfade bleiben erhalten, bis Login, Threads, Upload-Lifecycle, Turn und Backup-Hash
geprüft sind. Bei einem Extraktionsfehler nicht weiterstarten, sondern die verschobenen Pfade
zurückbenennen. Ein Workspace-Restore ist ein separates, explizites Verfahren und darf niemals als
Nebenwirkung eines Workbench-Rollbacks erfolgen.

## Vollständige Deinstallation ohne Datenverlust

1. Letztes konsistentes Backup samt Hash extern replizieren und testweise auflisten.
2. Workbench stoppen und nur dieses Compose-Projekt entfernen:

       cd /volume1/docker/codex/workspace/repositories/codex-workbench
       docker-compose down --remove-orphans

   Kein `down -v`, kein pauschales Docker-Prune und kein rekursives Löschen unter
   `/volume1/docker/codex` verwenden.

3. Nicht mehr benötigte Workbench-Images einzeln und anhand des exakten Tags entfernen:

       docker image rm codex-workbench-backend:<tag>
       docker image rm codex-workbench-frontend:<tag>

4. Persistente Workbench-Daten zunächst nur aus dem aktiven Pfad verschieben:

       deinstall_stamp=$(date -u +%Y%m%dT%H%M%SZ)
       mv /volume1/docker/codex/workbench "/volume1/docker/codex/workbench.retired-${deinstall_stamp}"

   Damit ist die Workbench entfernt, ihre Daten bleiben wiederherstellbar. Das gemeinsame
   `/volume1/docker/codex/config/.codex`, der Workspace, alle anderen Repositories, `backend-net` und
   der SSH-Codex-Container bleiben unangetastet. Das Workbench-Quellrepository erst nach separater
   Betreiberentscheidung archivieren; niemals den gesamten Workspace löschen.

## Noch offene Produktionsrisiken

- Der explizite HTTP-Modus ist ausschließlich für einen host-lokalen Test vorgesehen und schützt
  keine Transportdaten. Regulärer Betrieb erfordert den HTTPS Reverse Proxy.
- Das einmalige Setup kann vor dem Eigentümer von einem anderen LAN-Client übernommen werden.
- Geteiltes Codex-Home hat keine prozessübergreifende Schreibsperre; parallele CLI-Turns vermeiden.
- DSM-ACLs können restriktive POSIX-Modi erweitern; numerische UID/GID allein beweisen keinen
  wirksamen Zugriffsschutz.
- App-server und von Codex ausgeführte Prozesse laufen unter derselben Container-UID wie FastAPI.
  Full Access kann deshalb alle Backend-Mounts lesen, einschließlich Codex-Home und Workbench-
  Secrets. Read-only Secret-Mounts verhindern Änderung, nicht Lesen; dies ist ein bewusst offenes
  Privilege-Separation-Risiko.
- Container-Basistags sind versions-, aber noch nicht digestgepinnt. Registry-Tag-Mutation und
  Supply-Chain-Abweichung bleiben bis zu Digest-Pins, SBOM und Image-Scan offen.
- Login-Rate-Limits sehen hinter Nginx derzeit primär den Proxy-Peer. Das globale Limit bleibt
  wirksam; Forwarded-Client-IP wird absichtlich noch nicht als Autorität vertraut.
- Uploadbereinigung läuft bei Start und vor neuen Uploads, nicht als unabhängiger periodischer Job.
  Abgelaufene Dateien können daher bis zum nächsten dieser Ereignisse liegen bleiben.
- Tatsächliche linux/amd64-, seccomp/Landlock-, Netzwerk-, Health-, Recreate- und Last-Eigenschaften
  können erst auf der DS918+ freigegeben werden. Der verbindliche Ablauf steht im Testplan.
# Codex arg0 Temp-Hygiene

Workbench und Codex dürfen niemals als root gestartet werden. Fremde Eigentümer unter
`CODEX_HOME/tmp/arg0` können bereits bei `codex --version` eine stderr-Warnung erzeugen; die
Readiness-Prüfung weist dies absichtlich zurück. Installer und Diagnose melden diesen Zustand,
ändern ihn aber nicht. Eine Reparatur darf ausschließlich den flüchtigen Bereich
`CODEX_HOME/tmp/arg0` betreffen und muss auf dem Host mit der konfigurierten Workbench-UID/GID
erfolgen. Ein rekursives `chown` über das gesamte `CODEX_HOME` ist ausdrücklich unzulässig, weil
dort Authentifizierung, Konfiguration und Threaddaten liegen.
