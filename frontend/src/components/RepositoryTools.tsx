/* eslint-disable react-hooks/set-state-in-effect, jsx-a11y-x/no-autofocus */
import { useEffect, useRef, useState } from "react";
import type { GitHubCapability, NativeJob, Repository, RepositoryRemovalPreview } from "../workbench/types";
import type { RepositoryAction } from "./RepositorySidebar";
import { CopyButton } from "./CopyButton";

interface RepositoryToolsProps {
  action: RepositoryAction | null;
  repository: Repository | null;
  jobs: NativeJob[];
  github: GitHubCapability | null;
  pending: boolean;
  removalPreview: RepositoryRemovalPreview | null;
  onClose: () => void;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
  onRetryJob: (job: NativeJob) => Promise<void>;
}

export function RepositoryTools({ action, repository, jobs, github, pending, removalPreview, onClose, onSubmit, onRetryJob }: RepositoryToolsProps) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState("");
  const [branch, setBranch] = useState("");
  const [branchOperation, setBranchOperation] = useState<"create" | "switch">("create");
  const [selected, setSelected] = useState<string[]>([]);
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [confirmation, setConfirmation] = useState("");
  const [readme, setReadme] = useState(true);
  const [deleteThreads, setDeleteThreads] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (action !== null && dialog !== null && !dialog.open) dialog.showModal();
    if (action === null && dialog?.open) dialog.close();
    setName(repository?.name ?? ""); setSelected([]); setMessage(""); setBranch(""); setBranchOperation("create");
    setVisibility("private"); setConfirmation(""); setDeleteThreads(false);
  }, [action, repository]);

  if (action === null) return <GitActivityDrawer jobs={jobs} onRetryJob={onRetryJob} />;
  const changes = repository?.status.changes ?? [];
  const title = ({ create: "New repository", clone: "Clone repository", commit: "Commit changes", branches: "Branches", rename: "Rename display name", publish: "Publish to GitHub", status: "Git status", changes: "Changes", remote: "Remote", settings: "Repository settings", remove: "Aus Workbench entfernen", delete: "Lokales Repository löschen" } as Partial<Record<RepositoryAction,string>>)[action] ?? action;
  return <><dialog ref={dialogRef} className="decision-dialog repository-dialog" aria-labelledby="repository-tool-heading" onCancel={(event) => { event.preventDefault(); onClose(); }} onClose={onClose}>
    <h2 id="repository-tool-heading">{title}</h2>
    {action === "create" ? <><label>Name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label><label className="confirmation-check"><input type="checkbox" checked={readme} onChange={(event) => setReadme(event.target.checked)} /> README erstellen</label><p>Default branch: main</p></> : null}
    {action === "clone" ? <><label>Repository URL<input autoFocus value={url} onChange={(event) => { setUrl(event.target.value); if (!name) setName(deriveName(event.target.value)); }} placeholder="https://github.com/owner/repository.git" /></label><label>Local name<input value={name} onChange={(event) => setName(event.target.value)} /></label></> : null}
    {action === "commit" ? <><div className="change-picker" aria-label="Changes">{changes.map((change) => <label key={change.path}><input type="checkbox" checked={selected.includes(change.path)} onChange={(event) => setSelected((current) => event.target.checked ? [...current, change.path] : current.filter((item) => item !== change.path))} /><span>{change.path}</span><small>{change.staged ? "staged" : change.kind}</small></label>)}</div>{repository?.status.conflicts ? <p className="inline-error">Konflikte müssen vor dem Commit aufgelöst werden.</p> : null}<label>Commit message<textarea value={message} onChange={(event) => setMessage(event.target.value)} /></label></> : null}
    {action === "branches" ? <><p>Current: {repository?.status.detached ? "Detached HEAD" : repository?.status.branch}</p><fieldset><legend>Action</legend><label><input type="radio" checked={branchOperation === "create"} onChange={() => setBranchOperation("create")} /> Create local branch</label><label><input type="radio" checked={branchOperation === "switch"} onChange={() => setBranchOperation("switch")} /> Switch branch</label></fieldset><label>Branch name<input value={branch} onChange={(event) => setBranch(event.target.value)} /></label></> : null}
    {action === "rename" ? <><label>Display name<input autoFocus value={name} onChange={(event) => setName(event.target.value)} /></label><p>Directory und GitHub-Repository werden nicht umbenannt.</p></> : null}
    {action === "publish" ? github?.authenticated ? <><label>Owner<input value={github.account ?? ""} disabled /></label><label>Repository name<input value={name} onChange={(event) => setName(event.target.value)} /></label><fieldset><legend>Visibility</legend><label><input type="radio" checked={visibility === "private"} onChange={() => setVisibility("private")} /> Private</label><label><input type="radio" checked={visibility === "public"} onChange={() => setVisibility("public")} /> Public</label></fieldset>{visibility === "public" ? <div className="public-warning"><strong>Dieses Repository wird öffentlich im Internet sichtbar.</strong><label>Zur Bestätigung Repositorynamen eingeben<input value={confirmation} onChange={(event) => setConfirmation(event.target.value)} /></label></div> : null}</> : <p>GitHub CLI ist auf diesem Workbench-Host nicht authentifiziert.</p> : null}
    {action === "status" || action === "changes" ? <pre>{JSON.stringify(repository?.status, null, 2)}</pre> : null}
    {action === "remote" ? <p>{github?.remoteUrl ?? "Kein origin Remote konfiguriert."}</p> : null}
    {action === "remove" || action === "delete" ? <div className={`repository-removal ${action === "delete" ? "is-destructive" : ""}`}>
      <p>{action === "remove" ? "Das lokale Repository bleibt vollständig erhalten. Nur der Eintrag in dieser Workbench wird ausgeblendet." : "Das lokale Verzeichnis wird dauerhaft vom Workbench-Host entfernt. Remotes werden niemals verändert."}</p>
      {removalPreview === null ? <p role="status">Sicherheitsprüfung läuft …</p> : <>
        <dl><div><dt>Repository</dt><dd>{removalPreview.repositoryName}</dd></div><div><dt>Sicherer Zielpfad</dt><dd><code>{removalPreview.relativePath}</code></dd></div><div><dt>Zugeordnete Threads</dt><dd>{removalPreview.threadCount}</dd></div></dl>
        {removalPreview.hasUncommittedChanges ? <p className="public-warning"><strong>Warnung:</strong> Das Repository enthält nicht committete Änderungen.</p> : null}
        {removalPreview.blockedReasons.length > 0 ? <p className="inline-error" role="alert">Nicht möglich, solange ein Turn oder Git-Job für dieses Repository aktiv ist.</p> : null}
        <label className="confirmation-check"><input type="checkbox" checked={deleteThreads} onChange={(event) => setDeleteThreads(event.target.checked)} /> Zugeordnete Threads ebenfalls löschen (standardmäßig aus)</label>
        <label>Zur Bestätigung exakten Repositorynamen eingeben<input autoFocus value={confirmation} onChange={(event) => setConfirmation(event.target.value)} autoComplete="off" /></label>
      </>}
    </div> : null}
    <div className="dialog-actions"><button type="button" className="secondary-action" onClick={onClose}>Abbrechen</button>{["create","clone","commit","branches","rename","publish","remove","delete"].includes(action) ? <button type="button" className={action === "delete" ? "danger-action" : "primary-action"} disabled={pending || !canSubmit(action, { name, url, message, branch, selected, visibility, confirmation, repository, github, removalPreview })} onClick={() => void onSubmit({ name, url, message, branch, branchOperation, selected, visibility, confirmation, readme, deleteThreads })}>{pending ? "Wird ausgeführt …" : action === "commit" ? "Commit" : action === "publish" ? "Publish" : action === "remove" ? "Entfernen" : action === "delete" ? "Lokal löschen" : "Anwenden"}</button> : null}</div>
  </dialog><GitActivityDrawer jobs={jobs} onRetryJob={onRetryJob} /></>;
}

function GitActivityDrawer({ jobs, onRetryJob }: { jobs: NativeJob[]; onRetryJob: (job: NativeJob) => Promise<void> }) {
  const [dismissed, setDismissed] = useState<Set<string>>(() => new Set());
  const visible = jobs.filter((job) => !dismissed.has(job.id));
  if (visible.length === 0) return null;
  return <details className="git-activity-drawer"><summary>Git-Aktivität <span>{visible.filter((job) => job.status === "running" || job.status === "queued").length || visible.length}</span></summary><div>{visible.map((job) => <article key={job.id}><header><strong>{job.command || job.type}</strong><span className={`job-status is-${job.status}`}>{job.status}</span></header><small>{new Date(job.startedAt * 1000).toLocaleTimeString()} · {job.durationMs === null ? "läuft" : `${String(job.durationMs)} ms`} · Exit {job.exitCode ?? "–"}</small>{job.error ? <p className="inline-error">{job.error}</p> : null}{job.output ? <details className="job-output"><summary>Details</summary><div className="code-shell"><CopyButton text={job.output} label="Git-Output kopieren" /><pre>{job.output}</pre></div></details> : null}<div className="job-actions">{job.status === "failed" && job.repositoryId !== null && ["git_fetch","git_pull","git_push"].includes(job.type) ? <button type="button" onClick={() => void onRetryJob(job)}>Erneut versuchen</button> : null}{job.status === "succeeded" || job.status === "failed" ? <button type="button" onClick={() => setDismissed((current) => new Set(current).add(job.id))}>Ausblenden</button> : null}</div></article>)}</div></details>;
}

function deriveName(url: string): string { return url.split(/[/:]/).at(-1)?.replace(/\.git$/, "") ?? ""; }
function canSubmit(action: RepositoryAction, values: Record<string, unknown>): boolean {
  if (action === "create" || action === "clone" || action === "rename") return Boolean(values.name) && (action !== "clone" || Boolean(values.url));
  if (action === "commit") return Boolean(values.message) && (values.selected as string[]).length > 0 && !(values.repository as Repository | null)?.status.conflicts;
  if (action === "branches") return Boolean(values.branch);
  if (action === "publish") return Boolean(values.github) && Boolean(values.name) && (values.visibility !== "public" || values.confirmation === values.name);
  if (action === "remove" || action === "delete") {
    const preview = values.removalPreview as RepositoryRemovalPreview | null;
    return preview !== null && preview.blockedReasons.length === 0 && values.confirmation === preview.repositoryName;
  }
  return true;
}
