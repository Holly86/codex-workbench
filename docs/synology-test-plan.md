# Abnahmetest auf der Synology DS918+

Dieser Plan ist die Release-Gate-Checkliste für den realen linux/amd64-Host. Er wird nicht vom
Installationsskript ausgeführt. Jeden Punkt mit Datum, Tester, Ergebnis und einem secretsfreien
Beleg protokollieren. Fehler führen zum Abbruch; die Workbench bleibt bis zur Klärung nur im
über HTTPS eingeschränkten Reverse-Proxy-Testkreis erreichbar. TCP 8085 bleibt stets host-lokal.
Benötigt der DSM-Benutzer Rootzugriff auf den Docker-Daemon, gilt `sudo` für alle gezeigten
Docker-/Compose-Befehle.

## Testregeln und Vorbereitung

- Ausführungspfad ist
  `/volume1/docker/codex/workspace/repositories/codex-workbench`.
- Keine produktiven Repositories für Schreib-, Full-Access-, Approval- oder Interrupt-Tests nutzen.
- Vor Tests am geteilten Codex-Home alle aktiven Codex-CLI-Turns im SSH-Container beenden.
- In Screenshots und Testprotokollen keine Cookies, CSRF-Werte, Auth-Dateinamen/-inhalte, Prompts
  mit echten Daten oder vollständige private Pfade zeigen.
- Das externe Netz und der bestehende SSH-Codex-Container werden nicht verändert.
- Für jeden fehlgeschlagenen Test zunächst `docker-compose logs --tail=200` sichern; keine
  Container mit `kill -9` oder pauschalem Docker-Prune „reparieren“.
- Kein `docker system prune`, globales Image-/Netzwerk-/Volume-Prune, Stop/Recreate fremder
  Container, Entfernen von `backend-net` oder Ändern fremder Reverse-Proxies/Ports. Alle
  Compose-Befehle ausschließlich aus diesem Repository und mit Projektname `codex-workbench`.
- Daten von Alivo, Nextcloud, Paperless, n8n, ioBroker, Ollama, Pool, Signage und Homebridge werden
  weder gelesen noch verändert; `/volume1/docker/alivo` bleibt unangetastet.

Eine Ergebnistabelle verwenden:

| ID | Ergebnis | Beleg/Notiz |
| --- | --- | --- |
| S01 | offen | Compose-Auflösung |
| S02 | offen | Image-Build und Architektur |
| S03 | offen | Non-root und Härtung |
| S04 | offen | Mounts und Rechte |
| S05 | offen | Health/Readiness |
| S06 | offen | Bootstrap/Login/Origin |
| S07 | offen | WebSocket/Streaming/Activity |
| S08 | offen | Screenshot-Upload/Lifecycle |
| S09 | offen | Usage/Rate Limits |
| S10 | offen | Full Access + never |
| S11 | offen | on-request Approval |
| S12 | offen | Turn Interrupt |
| S13 | offen | App-Server-Crash/Reconnect |
| S14 | offen | Recreate/Persistenz |
| S15 | offen | Backup/Restore |
| S16 | offen | Graceful Shutdown/Logs |

## S01 – Host und `docker-compose config`

    cd /volume1/docker/codex/workspace/repositories/codex-workbench
    uname -m
    docker info --format '{{.OSType}} {{.Architecture}}'
    docker-compose version
    docker network inspect backend-net
    sudo ./scripts/init-deployment.sh
    docker-compose config --quiet
    docker-compose config > /tmp/codex-workbench-compose.resolved.yaml

Erwartung:

- Host ist Linux x86_64/amd64.
- Legacy-`docker-compose` akzeptiert Version 2.4 einschließlich `depends_on`-Healthbedingung,
  `init`, tmpfs-Optionen, Limits und externem Netz.
- Die aufgelöste Datei enthält nur absolute Quellen unter `/volume1/docker/codex`.
- Nur `frontend` enthält genau eine Portveröffentlichung mit `host_ip: 127.0.0.1`,
  `published: 8085` und `target: 8080`; es gibt keine weitere Veröffentlichung. Kein Service
  enthält `privileged`, Docker-Socket, Host-Netz oder feste Container-IP. `backend-net` ist
  `external: true`.
- Kein Service enthält `cpus`, `cpu_period`, `cpu_quota` oder gleichartige CPU-CFS-Schlüssel;
  `docker-net`, macvlan, `container_name` und feste IPs fehlen vollständig.
- Vor dem Start war Host-TCP-Port 8085 frei; der Installer hat sowohl diese Belegung als auch das
  vorhandene `backend-net` erfolgreich geprüft.
- `WORKBENCH_PUBLIC_ORIGIN` ist der reale HTTPS-Reverse-Proxy-Origin und kein `.invalid`-/
  `.example`-/TEST-NET-Platzhalter. Secure Cookie ist aktiv und unsicheres HTTP deaktiviert.
- `.env` ist LF-normalisiert und Modus 0600. Die aufgelöste Datei nicht veröffentlichen, obwohl
  `.env` keine Credentials enthalten darf.

## S02 – Image-Build, Plattform und Codex-Pin

    docker-compose build --pull --no-cache
    workbench_tag=$(sed -n 's/^WORKBENCH_IMAGE_TAG=//p' .env)
    docker image inspect --format '{{.Os}} {{.Architecture}} {{.Config.User}}' "codex-workbench-backend:${workbench_tag}"
    docker image inspect --format '{{.Os}} {{.Architecture}} {{.Config.User}}' "codex-workbench-frontend:${workbench_tag}"
    docker-compose run --rm --no-deps backend /usr/local/bin/codex --version
    docker-compose run --rm --no-deps backend python --version
    docker-compose run --rm --no-deps backend node --version
    docker-compose run --rm --no-deps backend npm --version
    docker-compose run --rm --no-deps backend sh -c 'command -v bash; command -v git; command -v make; command -v rg; command -v ssh; command -v ps'

Erwartung: beide Images `linux amd64`; Backend-Image ist non-root, Codex meldet exakt
`codex-cli 0.146.0`, Python 3.12.11 und Node 24.18.1. Repositorywerkzeuge und Zertifikatspaket sind
im Image vorhanden; `codex app-server --help` muss erfolgreich sein. Die Buildx-Meldung zu
fehlendem Host-Git ist zulässig, da keine Commitmetadaten ins Image erfunden werden und Runtime
nicht davon abhängt. Git wird dafür nicht auf DSM installiert. `docker-compose run` erstellt
kurzlebige Testcontainer, startet aber nicht die regulären Services.

## S03 – Start, Non-root, read-only Root und Containerhärtung

    docker-compose up -d --no-build --remove-orphans
    docker-compose ps
    docker-compose exec -T backend id
    docker-compose exec -T frontend id
    backend_id=$(docker-compose ps -q backend)
    frontend_id=$(docker-compose ps -q frontend)
    docker inspect --format 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} privileged={{.HostConfig.Privileged}} caps={{json .HostConfig.CapDrop}} pids={{.HostConfig.PidsLimit}}' "$backend_id"
    docker inspect --format 'user={{.Config.User}} readonly={{.HostConfig.ReadonlyRootfs}} privileged={{.HostConfig.Privileged}} caps={{json .HostConfig.CapDrop}} pids={{.HostConfig.PidsLimit}}' "$frontend_id"
    docker inspect --format 'backend ports={{json .NetworkSettings.Ports}} tmpfs={{json .HostConfig.Tmpfs}}' "$backend_id"
    docker inspect --format 'frontend ports={{json .NetworkSettings.Ports}} tmpfs={{json .HostConfig.Tmpfs}}' "$frontend_id"

Erwartung: beide Prozesse laufen verbindlich als `1026:100`; `readonly=true`, `privileged=false`,
`CapDrop` enthält `ALL`, PID-/Speicher-/nofile-Limits sind gesetzt. CPU-CFS-Quotas fehlen bewusst,
weil der DS918+-Kernel sie nicht unterstützt. Backend-Ports haben keine Hostbindung und der Codex App Server bleibt ein reiner
stdio-Kindprozess. Frontend veröffentlicht ausschließlich `127.0.0.1:8085->8080/tcp`; weder
`0.0.0.0` noch eine LAN-IP darf in der Portbindung erscheinen.

Die folgenden Schreibversuche müssen fehlschlagen:

    if docker-compose exec -T backend sh -c ': > /app/rootfs-write-test'; then echo 'FEHLER: Backend-Root beschreibbar'; else echo 'OK: Backend-Root read-only'; fi
    if docker-compose exec -T frontend sh -c ': > /usr/share/nginx/html/rootfs-write-test'; then echo 'FEHLER: Frontend-Root beschreibbar'; else echo 'OK: Frontend-Root read-only'; fi
    if docker-compose exec -T backend sh -c ': > /run/secrets/workbench/secret-write-test'; then echo 'FEHLER: Secret-Mount beschreibbar'; else echo 'OK: Secret-Mount read-only'; fi

## S04 – Persistente Mounts, Workspace und Codex-Home

Mountliste prüfen, ohne Dateiinhalte auszugeben:

    docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' "$backend_id"
    docker inspect --format '{{range .Mounts}}{{println .Source "->" .Destination "rw=" .RW}}{{end}}' "$frontend_id"
    docker-compose exec -T backend sh -c 'test -r /home/workbench/.codex && test -w /home/workbench/.codex'
    docker-compose exec -T backend sh -c 'test -r /workspace/repositories && test -w /workspace/repositories'
    docker-compose exec -T backend sh -c 'test -r /run/secrets/workbench/session.key && test ! -w /run/secrets/workbench/session.key'

Temporäre Schreibproben erzeugen und sofort entfernen:

    docker-compose exec -T backend sh -c 'set -eu; umask 077; for directory in /var/lib/codex-workbench/state /var/lib/codex-workbench/uploads /var/lib/codex-workbench/security /home/workbench/.codex /workspace/repositories; do probe="$directory/.workbench-mount-probe"; : > "$probe"; test -f "$probe"; rm -f "$probe"; done'

Erwartung: nur Backend erhält die sechs dokumentierten Hostmounts; Frontend keinen. Kein Mountziel
oder Quelle enthält `docker.sock`. Die Tests verändern keine bestehenden Inhalte. Danach auf dem
Host Eigentümer/Modi und DSM-ACLs prüfen:

    stat -c '%u:%g %a %n' \
      /volume1/docker/codex/workbench/data \
      /volume1/docker/codex/workbench/data/state \
      /volume1/docker/codex/workbench/uploads \
      /volume1/docker/codex/workbench/secrets/password \
      /volume1/docker/codex/workbench/secrets/session \
      /volume1/docker/codex/workbench/secrets/session/session.key

## S05 – Health und Readiness

    docker-compose ps
    curl --fail --silent --show-error http://127.0.0.1:8085/healthz
    curl --fail --silent --show-error http://127.0.0.1:8085/api/health/live
    curl --fail --silent --show-error http://127.0.0.1:8085/api/health/ready
    public_origin=$(sed -n 's/^WORKBENCH_PUBLIC_ORIGIN=//p' .env)
    curl --fail --silent --show-error "$public_origin/healthz"
    curl --fail --silent --show-error "$public_origin/api/health/live"
    curl --fail --silent --show-error "$public_origin/api/health/ready"
    sudo ./scripts/diagnose-deployment.sh

Die drei HTTP-Aufrufe werden auf der Synology selbst ausgeführt. Von einem anderen LAN-Host müssen
`http://<NAS-LAN-IP>:8085/healthz` und TCP-Verbindungen zu Port 8085 scheitern. Die drei HTTPS-
Aufrufe laufen über den Reverse Proxy. Erwartung: jeweils HTTP 200; Ready-JSON meldet `workspace`,
`repositories`, `data`, `security` und `app_server` als `true`. Backend wird in
`docker-compose ps` `healthy`. Wenn der App-Server wegen Authentifizierung zwar initialisiert, aber
noch keinen Turn ausführen kann, bleibt der spätere Turntest das verbindliche Auth-Gate.

Bei einem Versionfehler muss der Log genau einen redigierten Grund aus `binary_not_found`,
`execution_failed`, `malformed_version_output`, `unsupported_version`, `timeout`,
`permission_denied` oder `unexpected_stderr` zeigen, niemals rohe stdout-/stderr-Zeilen. Der Probe
akzeptiert nur die einzelne Zeile `codex-cli 0.146.0`; `/ready` bleibt dabei korrekt 503.

Die Reverse-Proxy-Regel muss als Ziel `http://127.0.0.1:8085` verwenden, HTTP/1.1-WebSockets mit
`Upgrade`/`Connection` weiterleiten, den ursprünglichen `Host` erhalten, Forwarded-Proto auf `https`
setzen und den Browser-`Origin` unverändert lassen. Im Browser-Netzwerkpanel dürfen `/api` und `/ws`
nur unter demselben Origin aus `.env` erscheinen; eine direkte LAN-Regel oder NAT-Freigabe für 8085
darf nicht existieren.

## S06 – Bootstrap, Login und Browsergrenze

1. Zugriff auf die HTTPS-Reverse-Proxy-Quelle vorübergehend auf den Tester begrenzen; keine direkte
   Portfreigabe für 8085 anlegen.
2. Exakten Origin öffnen; Setup mit Test-Produktionspasswort durchführen.
3. Prüfen, dass `password.argon2id` entstanden ist, ohne Inhalt auszugeben:

       stat -c '%u:%g %a %s %n' /volume1/docker/codex/workbench/secrets/password/password.argon2id

4. Abmelden, Browser neu laden und erfolgreich anmelden. Setup darf nicht erneut erscheinen.
5. Falsches Passwort mehrfach testen: generische Fehlermeldung, Verzögerung und globales Limit
   beobachten; danach das Zeitfenster ablaufen lassen. Keine Last-/Bruteforce-Schleife verwenden.
6. Zugriff über eine andere IP/Host-Schreibweise als den kanonischen Origin muss für mutierende
   Aktionen beziehungsweise WebSocket scheitern. Im Browser-Netzwerkpanel prüfen, dass
   `/ws/events` nur mit dem exakten `Origin` auf HTTP 101 wechselt. Cookies sind `HttpOnly`,
   `SameSite=Strict`, ohne Domain; unter HTTPS zusätzlich `Secure` und `__Host-`-präfigiert.

## Disposable Testrepository

Vor S07 bis S13 einmalig ein ausschließlich dafür bestimmtes Repository als Containerbenutzer
anlegen. Der Befehl verweigert die Ausführung, wenn der Pfad bereits existiert:

    docker-compose exec -T backend sh -c 'set -eu; test ! -e /workspace/repositories/workbench-deployment-smoke; mkdir /workspace/repositories/workbench-deployment-smoke; git -C /workspace/repositories/workbench-deployment-smoke init -q; printf "%s\n" "# Workbench deployment smoke" > /workspace/repositories/workbench-deployment-smoke/README.md'

Im UI neu laden und ausschließlich `workbench-deployment-smoke` für Schreibtests wählen. Nach der
Abnahme das Repository nicht blind löschen; als Testartefakt außerhalb des Repositoryroots
archivieren oder nach manueller Inhaltsprüfung gezielt entfernen.

## S07 – Turnstart, WebSocket, Assistant-Streaming und Activity

1. Testrepository wählen, `Workspace write` und `On request` setzen, neuen Thread öffnen.
2. Browser-Entwicklerwerkzeuge öffnen. `/ws/events` muss Status 101 haben; erste Nachricht ist ein
   Snapshot, danach streng steigende Sequenzen und Heartbeats.
3. Aufgabe senden: „Lies README.md und antworte schrittweise mit einer kurzen Zusammenfassung.“
4. Prüfen: Turn startet einmal; „Working…“ erscheint; Assistant-Text wächst inkrementell; Activity
   zeigt sinnvolle letzte Aktivität; item/completed ersetzt provisorische Inhalte; terminaler Zustand
   wird genau einmal angezeigt.
5. Netzwerk kurz trennen und wieder verbinden. UI muss Snapshot/Reconnect statt doppelter Deltas
   zeigen. Ein absichtlich falscher Origin darf keine WebSocket-Verbindung erhalten.

## S08 – Screenshot-Paste und Upload-Lifecycle

1. Ein kleines eindeutiges PNG in die Zwischenablage kopieren und in den Composer einfügen.
2. Fortschritt, Vorschau und Zustand „Bereit“ abwarten. Mit Bild und Text senden; der Assistant muss
   das Bild erkennbar berücksichtigen.
3. Zweites Bild einfügen und vor Ende abbrechen. Es darf nicht als sendbar erscheinen; Stagingdatei
   muss verschwinden und Metadaten den Abbruch festhalten.
4. Ein animiertes GIF, ein SVG und eine Datei über 20 MiB getrennt testen: sichere Ablehnung ohne
   Vorschau als bereit.
5. Hostseitig ausschließlich Metadaten prüfen:

       find /volume1/docker/codex/workbench/uploads -maxdepth 1 -type f -exec stat -c '%u:%g %a %s %n' {} \;

   Erwartung: zufällige Namen, `1026:100`, Modus 600. Keine Datei ist statisch per Browser-URL
   abrufbar. Für TTL-Cleanup einen ungenutzten Upload auslaufen lassen, danach einen neuen Upload
   anstoßen oder Backend regulär neu starten und Entfernung prüfen. SQLite-Metadaten dürfen als
   Lifecycle-Historie verbleiben.

## S09 – Usage und Rate Limits

Nach mindestens einem vollständigen Turn „Nutzung & Kontingent“ öffnen:

- Latest-, Thread-total- und Kontextwerte müssen mit eingegangenen Protocollevents wachsen.
- Primär-/Sekundärfenster, Resetzeit, Credits und Exhaustion werden nur angezeigt, wenn
  `account/rateLimits/read` Werte liefert.
- Bei nicht verfügbarem Accountwert steht „nicht verfügbar“, niemals ein aus Tokens erfundener Wert.
- Einen Account nahe einem realen Limit nur verwenden, wenn bereits vorhanden. Kein bezahltes
  Kontingent künstlich aufbrauchen. Bei gemeldetem Limit muss der UI-Zustand sichtbar und ein Turn
  sauber fehlgeschlagen/abgewiesen sein; nach Reset erneut lesen.
- Die UI-Schwelle „nahe Limit“ ab 90 Prozent ausdrücklich nicht als offiziellen OpenAI-Status werten.

## S10 – Full Access mit `never`

Nur im disposable Repository:

1. Sandbox `Full Access` und Approval Policy `Never` wählen.
2. Dialog muss Repository, `danger-full-access` und `never` nennen. Ohne Checkbox darf der
   Aktivierungsbutton nicht funktionieren.
3. Bestätigen und genau einen Turn starten: „Erzeuge `full-access-never.txt` mit dem Inhalt
   `workbench-smoke`; lies die Datei zurück.“
4. Es darf kein Einzel-Approval erscheinen. Thread und laufender Turn müssen dauerhaft sichtbar als
   Full Access gekennzeichnet sein. Datei muss im disposable Repository liegen.
5. Neue Full-Access-Auswahl muss eine neue Einmalbestätigung verlangen; Replay der alten
   Bestätigung darf nicht wirken.

Dieser Test beweist Codex-Modus und UI-Bindung, nicht Host-Isolation. Full Access kann alle für UID
1026 lesbaren Backend-Mounts sehen; keine echten Secrets in Prompts aufnehmen.

## S11 – Approval-Modus `on-request`

1. Neuen Thread mit `Workspace write` und `On request` öffnen.
2. Eine eindeutig approvalpflichtige Netzwerkaktion anfordern, beispielsweise ein `git ls-remote`
   gegen ein öffentliches Testrepository. Die Aktion noch nicht genehmigen.
3. Dialog muss sanitisierten Befehl, Repository, Grund, Ablaufzeit und nur serverseitig angebotene
   Entscheidungen zeigen. Zuerst `Decline` wählen; keine Netzwerkaktion darf ausgeführt werden.
4. Mit neuer Anfrage wiederholen und einmalig `Accept` wählen. Genau eine Protokollantwort und ein
   terminales Item erwarten. Browser-Reload, Logout oder abgelaufene Anfrage müssen ein altes
   Approval invalidieren; Replay muss scheitern.
5. `Accept for session` oder Policy-Amendments nur testen, wenn die UI sie ausdrücklich stärker
   kennzeichnet und der Testumfang dies verlangt.

## S12 – Turn Interrupt

Einen absichtlich längeren, aber harmlosen Lese-/Analyse-Turn im disposable Repository starten.
Während Assistant oder Activity streamt „Stop“ wählen. Erwartung:

- UI wechselt zunächst auf „wird unterbrochen“ und nicht sofort auf erfolgreich.
- Backend sendet `turn/interrupt` mit Thread- und Turn-ID.
- Erst terminales `turn/completed` zeigt `interrupted` oder einen anderen autoritativen Endstatus.
- Keine weiteren Assistant-Deltas werden dem beendeten Turn zugeordnet; danach ist ein neuer Turn
  möglich.

## S13 – App-Server-Crash und Reconnect

1. Keine echte Schreibaktion laufen lassen. Prozesse anzeigen:

       docker-compose exec -T backend ps -eo pid,ppid,args

2. PID des exakten Kindprozesses mit Argumenten `app-server --stdio` manuell identifizieren. PID 1
   ist Uvicorn/Init und darf nicht verwendet werden. Dann gezielt:

       docker-compose exec -T backend kill -TERM <geprüfte-app-server-pid>

3. Erwartung: Backend-Container bleibt am Leben; Readiness wird vorübergehend 503/unhealthy;
   Weboberfläche zeigt Reconnecting; offene Approvals werden invalidiert; begrenzter Backoff startet
   eine neue Generation; Ready kehrt auf 200 zurück und Threads werden abgeglichen.
4. Logs enthalten nur Event-/Fehlertyp und Generation, keinen rohen stderr-Inhalt. Nach fünf
   absichtlichen Crashes im konfigurierten Fenster greift das Restartlimit; diese Grenzprüfung nur in
   einem gesonderten Wartungsfenster durchführen und danach den Backend-Container regulär neu
   erstellen.

## S14 – Container-Recreate und Persistenz

Vorher keine laufenden Turns. Prüfsummen der unveränderlichen Workbench-Secrets erfassen:

    sudo sha256sum \
      /volume1/docker/codex/workbench/secrets/session/session.key \
      /volume1/docker/codex/workbench/secrets/password/password.argon2id \
      > /tmp/codex-workbench-secrets.before
    docker-compose up -d --no-build --force-recreate
    sudo sha256sum -c /tmp/codex-workbench-secrets.before

Danach Health und Login prüfen. Vorhandene Workbench-Threadreferenz, Codex-Thread, disposable Dateien
und Uploadmetadaten müssen erhalten sein. Container-IDs müssen neu, Hostpfade und Secret-Hashes
gleich sein. Temporäre tmpfs-Caches dürfen leer sein. SQLite selbst nicht im laufenden Betrieb per
Hash vergleichen, weil Sessions/WAL legitime Änderungen erzeugen.

## S15 – Backup und Restore

1. Exakt das Backupverfahren aus [deployment.md](deployment.md#backup) in einem Wartungsfenster
   ausführen; Hashprüfung muss erfolgreich sein.
2. Archiv ohne Ausgabe sensibler Dateiinhalte auflisten und in ein neues restriktives Testverzeichnis
   unter `/volume1/docker/codex/workbench` extrahieren:

       workbench_backup=<absoluter-pfad-zum-geprüften-control-archiv>.tar.gz
       restore_test=$(mktemp -d /volume1/docker/codex/workbench/restore-test.XXXXXX)
       chmod 700 "$restore_test"
       tar -C "$restore_test" -xzpf "$workbench_backup"
       test -f "$restore_test/workbench/data/state/workbench.sqlite3"
       test -f "$restore_test/workbench/secrets/session/session.key"
       test -f "$restore_test/workbench/secrets/password/password.argon2id"
       test -d "$restore_test/config/.codex"
       test -f "$restore_test/workspace/repositories/codex-workbench/.env"

3. Testverzeichnis bis zum Abschluss als Beleg behalten und danach nur den durch `mktemp` konkret
   ausgegebenen Pfad nach manueller Prüfung entfernen.
4. Vor Produktionsfreigabe einmal den vollständigen Restoreablauf mit den `pre-restore`-
   Quarantänepfaden durchführen. Login, Threads, Testrepository, Screenshot, Turn und Secret-Hashes
   müssen danach funktionieren. Den allgemeinen Workspace nicht als Teil dieses Restoretests
   überschreiben.

## S16 – Graceful Shutdown und Log-Leak-Test

1. Einen synthetischen, einzigartigen Marker ohne echtes Secret als Prompt senden, zum Beispiel
   `WB_LOG_LEAK_TEST_20260731`, und den Turn abschließen.
2. Einen Turn starten, über UI unterbrechen und danach regulär stoppen:

       docker-compose stop -t 30
       docker-compose logs --no-color --tail=500 backend frontend > /tmp/codex-workbench-safe-log-review.txt
       if grep -F 'WB_LOG_LEAK_TEST_20260731' /tmp/codex-workbench-safe-log-review.txt; then echo 'FEHLER: Promptmarker im Log'; else echo 'OK: kein Promptmarker'; fi
       grep -E 'application_stopped|app_server' /tmp/codex-workbench-safe-log-review.txt

3. Log manuell auf Prompttext, Repository-Absolutpfade, Uploadnamen/-inhalte, Cookie/CSRF,
   Passwort-Hash, Codex-Auth und rohe Environment-Dumps prüfen. Nginx darf keine Access-Logzeilen
   erzeugen. Erwartung: `application_stopped`, kein SIGKILL und kein verwaister app-server-Prozess.
4. Wieder starten und Abschlusshealth prüfen:

       docker-compose up -d --no-build
       docker-compose ps

## Freigabeentscheidung

Produktionsfreigabe erst, wenn S01–S16 bestanden sind oder jede nicht ausführbare Grenzbedingung
mit Risikoakzeptanz dokumentiert wurde. Besonders linux/amd64-Build, nicht-root Mountzugriff,
read-only Root, authentifizierter echter Turn, Full Access/Approval, Crash-Reconnect, Recreate und
Restore dürfen nicht allein durch lokale Fake-Child-Tests ersetzt werden.
