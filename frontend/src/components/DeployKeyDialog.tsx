/* eslint-disable react-hooks/set-state-in-effect, jsx-a11y-x/no-autofocus */
import { useEffect, useRef, useState } from "react";

import {
  clonePrivateRepository,
  connectPrivateRepository,
  createRepositoryDeployKey,
  deployKeyRemotePreview,
  promoteDeployKey,
  readRepositoryDeployKey,
  removeDeployKey,
  removeRetiredDeployKey,
  rotateDeployKey,
  switchOriginToDeployKey,
  testDeployKey,
} from "../workbench/api";
import type { DeployKeyRemotePreview, DeployKeyView, Repository } from "../workbench/types";
import { CopyButton } from "./CopyButton";

interface DeployKeyDialogProps {
  mode: "repository" | "private-clone" | null;
  repository: Repository | null;
  repositoryNames: readonly string[];
  csrfToken: string;
  onClose: () => void;
  onCloneStarted: () => Promise<void>;
}

export function DeployKeyDialog({
  mode,
  repository,
  repositoryNames,
  csrfToken,
  onClose,
  onCloneStarted,
}: DeployKeyDialogProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [deployKey, setDeployKey] = useState<DeployKeyView | null>(null);
  const [githubRepository, setGithubRepository] = useState("");
  const [localName, setLocalName] = useState("");
  const [remotePreview, setRemotePreview] = useState<DeployKeyRemotePreview | null>(null);
  const [remoteConfirmation, setRemoteConfirmation] = useState("");
  const [removeConfirmed, setRemoveConfirmed] = useState(false);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const showError = (caught: unknown) => {
    setError(caught instanceof Error ? caught.message : "Die Deploy-Key-Aktion ist fehlgeschlagen.");
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (mode !== null && dialog !== null && !dialog.open) dialog.showModal();
    if (mode === null && dialog?.open) dialog.close();
    setDeployKey(null);
    setGithubRepository("");
    setLocalName(repository?.directoryName ?? "");
    setRemotePreview(null);
    setRemoteConfirmation("");
    setRemoveConfirmed(false);
    setMessage(null);
    setError(null);
    if (mode === "repository" && repository !== null) {
      setPending(true);
      void readRepositoryDeployKey(repository.id)
        .then(setDeployKey)
        .catch(showError)
        .finally(() => setPending(false));
    }
  }, [mode, repository]);

  if (mode === null) return null;

  const trimmedLocalName = localName.trim();
  const knownNameConflict = mode === "private-clone"
    && repositoryNames.includes(trimmedLocalName);

  const run = async (operation: () => Promise<void>) => {
    setPending(true);
    setError(null);
    setMessage(null);
    try {
      await operation();
    } catch (caught: unknown) {
      showError(caught);
    } finally {
      setPending(false);
    }
  };

  const create = () => run(async () => {
    const created = mode === "repository" && repository !== null
      ? await createRepositoryDeployKey(csrfToken, repository.id)
      : await connectPrivateRepository(csrfToken, githubRepository);
    setDeployKey(created);
    if (mode === "private-clone") {
      setLocalName(created.githubRepository.split("/").at(-1) ?? "");
    }
    setMessage("Deploy Key ist verfügbar. Hinterlege bei Bedarf ausschließlich den Public Key bei GitHub.");
  });

  const test = (candidate: boolean) => run(async () => {
    if (deployKey === null) return;
    const result = await testDeployKey(csrfToken, deployKey.credentialId, candidate);
    setDeployKey(result.deployKey);
    setMessage(result.succeeded
      ? "Zugriff auf exakt dieses GitHub-Repository wurde erfolgreich geprüft."
      : "Zugriff fehlgeschlagen. Prüfe den Deploy Key in den GitHub-Einstellungen.");
  });

  return <dialog
    ref={dialogRef}
    className="decision-dialog deploy-key-dialog"
    aria-labelledby="deploy-key-heading"
    onCancel={(event) => { event.preventDefault(); onClose(); }}
    onClose={onClose}
  >
    <h2 id="deploy-key-heading">{mode === "repository" ? "SSH / Deploy Key" : "Privates GitHub-Repository verbinden"}</h2>
    {mode === "private-clone" && deployKey === null ? <>
      <p>Erzeuge einen dedizierten Ed25519 Deploy Key für genau ein Repository auf github.com.</p>
      <label>GitHub Repository URL oder OWNER/REPO
        <input autoFocus value={githubRepository} onChange={(event) => setGithubRepository(event.target.value)} placeholder="owner/private-repository" />
      </label>
    </> : null}
    {mode === "repository" && deployKey === null && !pending ? <p>Für dieses Repository ist noch kein Workbench Deploy Key eingerichtet.</p> : null}
    {deployKey !== null ? <>
      <dl className="deploy-key-summary">
        <div><dt>GitHub-Repository</dt><dd>{deployKey.githubRepository}</dd></div>
        <div><dt>Status</dt><dd>{statusLabel(deployKey.status)}</dd></div>
        <div><dt>Fingerprint</dt><dd><code>{deployKey.active.fingerprint}</code></dd></div>
        <div><dt>Erstellt</dt><dd>{formatTime(deployKey.active.createdAt)}</dd></div>
        <div><dt>Letzter erfolgreicher Test</dt><dd>{formatTime(deployKey.lastSuccessfulTestAt)}</dd></div>
      </dl>
      <section className="deploy-key-public" aria-labelledby="active-key-heading">
        <h3 id="active-key-heading">Public Key</h3>
        <div className="code-shell"><CopyButton text={deployKey.active.publicKey} label="Public Key kopieren" /><pre>{deployKey.active.publicKey}</pre></div>
      </section>
      <div className="deploy-key-instructions">
        <strong>Bei GitHub hinterlegen</strong>
        <ol><li>Repository öffnen</li><li>Settings → Deploy keys</li><li>Add deploy key</li><li>Public Key einfügen</li><li>Optional „Allow write access“ aktivieren</li></ol>
        <p>Die Workbench installiert oder entfernt keinen GitHub Deploy Key automatisch. Schreibzugriff wird nicht behauptet.</p>
      </div>
      <div className="deploy-key-actions">
        <button type="button" className="secondary-action" disabled={pending} onClick={() => void test(false)}>Zugriff testen</button>
        {mode === "repository" && deployKey.candidate === null ? <button type="button" className="secondary-action" disabled={pending} onClick={() => void run(async () => setDeployKey(await rotateDeployKey(csrfToken, deployKey.credentialId)))}>Key rotieren</button> : null}
      </div>
      {deployKey.candidate !== null ? <section className="deploy-key-rotation">
        <h3>Neuer Key für Rotation</h3>
        <p>Der bisherige lokale Key bleibt aktiv, bis dieser neue Key bei GitHub hinterlegt und erfolgreich getestet wurde.</p>
        <div className="code-shell"><CopyButton text={deployKey.candidate.publicKey} label="Neuen Public Key kopieren" /><pre>{deployKey.candidate.publicKey}</pre></div>
        <div className="deploy-key-actions">
          <button type="button" className="secondary-action" disabled={pending} onClick={() => void test(true)}>Neuen Key testen</button>
          <button type="button" className="primary-action" disabled={pending || deployKey.status !== "rotation_verified"} onClick={() => void run(async () => setDeployKey(await promoteDeployKey(csrfToken, deployKey.credentialId)))}>Verifizierten Key aktivieren</button>
        </div>
      </section> : null}
      {deployKey.retiredKeyAvailable ? <div className="deploy-key-warning"><p>Der vorherige lokale Key ist noch als Rückfalloption gespeichert. Ein GitHub-Key muss separat in GitHub entfernt werden.</p><button type="button" className="secondary-action" disabled={pending} onClick={() => void run(async () => setDeployKey(await removeRetiredDeployKey(csrfToken, deployKey.credentialId)))}>Alten lokalen Key entfernen</button></div> : null}
      {mode === "private-clone" ? <div className="private-clone-controls">
        <label>Lokaler Repositoryname<input value={localName} onChange={(event) => { setLocalName(event.target.value); setError(null); }} /></label>
        {knownNameConflict ? <p role="alert" className="inline-error">Dieser lokale Repositoryname wird bereits verwendet.</p> : null}
        <button type="button" className="primary-action" disabled={pending || deployKey.status !== "verified" || !trimmedLocalName || knownNameConflict} onClick={() => void run(async () => { await clonePrivateRepository(csrfToken, trimmedLocalName, deployKey.credentialId); await onCloneStarted(); onClose(); })}>Geprüften privaten Clone starten</button>
      </div> : null}
      {mode === "repository" && repository !== null && deployKey.status === "verified" ? <div className="deploy-key-remote">
        {remotePreview === null ? <button type="button" className="secondary-action" disabled={pending} onClick={() => void run(async () => setRemotePreview(await deployKeyRemotePreview(repository.id)))}>origin auf Deploy-Key-SSH umstellen …</button> : <>
          <dl><div><dt>Aktuelle URL</dt><dd><code>{remotePreview.currentUrl}</code></dd></div><div><dt>Neue URL</dt><dd><code>{remotePreview.newUrl}</code></dd></div></dl>
          <label>Zur Bestätigung neue URL exakt eingeben<input value={remoteConfirmation} onChange={(event) => setRemoteConfirmation(event.target.value)} /></label>
          <button type="button" className="primary-action" disabled={pending || remoteConfirmation !== remotePreview.newUrl} onClick={() => void run(async () => { await switchOriginToDeployKey(csrfToken, repository.id, remoteConfirmation); setMessage("origin wurde auf die repositorygebundene Deploy-Key-Identity umgestellt."); setRemotePreview(null); })}>origin umstellen</button>
        </>}
      </div> : null}
      <label className="confirmation-check"><input type="checkbox" checked={removeConfirmed} onChange={(event) => setRemoveConfirmed(event.target.checked)} /> Ich verstehe, dass ein Deploy Key in GitHub separat entfernt werden muss.</label>
      <button type="button" className="danger-action" disabled={pending || !removeConfirmed} onClick={() => void run(async () => { await removeDeployKey(csrfToken, deployKey.credentialId); setDeployKey(null); setMessage("Lokales Deploy-Key-Material wurde entfernt."); })}>Lokalen Key entfernen</button>
    </> : null}
    {message !== null ? <p role="status" className="auth-notice">{message}</p> : null}
    {error !== null ? <p role="alert" className="inline-error">{error}</p> : null}
    <div className="dialog-actions">
      <button type="button" className="secondary-action" onClick={onClose}>Schließen</button>
      {deployKey === null ? <button type="button" className="primary-action" disabled={pending || (mode === "private-clone" && !githubRepository.trim())} onClick={() => void create()}>{pending ? "Wird erzeugt …" : "Deploy Key erzeugen"}</button> : null}
    </div>
  </dialog>;
}

function statusLabel(status: DeployKeyView["status"]): string {
  if (status === "verified") return "Zugriff geprüft";
  if (status === "failed") return "Zugriff fehlgeschlagen";
  if (status === "rotation_verified") return "Neuer Key geprüft";
  return "Eingerichtet";
}

function formatTime(value: number | null): string {
  return value === null ? "nicht geprüft" : new Date(value * 1000).toLocaleString();
}
