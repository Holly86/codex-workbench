/* eslint-disable react-hooks/set-state-in-effect, jsx-a11y-x/no-autofocus */
import { useEffect, useRef, useState } from "react";
import type { GitHubCapability, NativeJob, Repository } from "../workbench/types";
import type { RepositoryAction } from "./RepositorySidebar";
import { CopyButton } from "./CopyButton";

interface RepositoryToolsProps {
  action: RepositoryAction | null;
  repository: Repository | null;
  jobs: NativeJob[];
  github: GitHubCapability | null;
  pending: boolean;
  onClose: () => void;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
}

export function RepositoryTools({ action, repository, jobs, github, pending, onClose, onSubmit }: RepositoryToolsProps) {
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

  useEffect(() => {
    const dialog = dialogRef.current;
    if (action !== null && dialog !== null && !dialog.open) dialog.showModal();
    if (action === null && dialog?.open) dialog.close();
    setName(repository?.name ?? ""); setSelected([]); setMessage(""); setBranch(""); setBranchOperation("create");
    setVisibility("private"); setConfirmation("");
  }, [action, repository]);

  if (action === null) return <GitActivityDrawer jobs={jobs} />;
  const changes = repository?.status.changes ?? [];
  const title = ({ create: "New repository", clone: "Clone repository", commit: "Commit changes", branches: "Branches", rename: "Rename display name", publish: "Publish to GitHub", status: "Git status", changes: "Changes", remote: "Remote", settings: "Repository settings" } as Partial<Record<RepositoryAction,string>>)[action] ?? action;
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
    <div className="dialog-actions"><button type="button" className="secondary-action" onClick={onClose}>Cancel</button>{["create","clone","commit","branches","rename","publish"].includes(action) ? <button type="button" className="primary-action" disabled={pending || !canSubmit(action, { name, url, message, branch, selected, visibility, confirmation, repository, github })} onClick={() => void onSubmit({ name, url, message, branch, branchOperation, selected, visibility, confirmation, readme })}>{pending ? "Working…" : action === "commit" ? "Commit" : action === "publish" ? "Publish" : "Apply"}</button> : null}</div>
  </dialog><GitActivityDrawer jobs={jobs} /></>;
}

function GitActivityDrawer({ jobs }: { jobs: NativeJob[] }) {
  if (jobs.length === 0) return null;
  return <details className="git-activity-drawer"><summary>Git activity <span>{jobs.filter((job) => job.status === "running" || job.status === "queued").length || jobs.length}</span></summary><div>{jobs.map((job) => <article key={job.id}><header><strong>{job.command || job.type}</strong><span className={`job-status is-${job.status}`}>{job.status}</span></header><small>{new Date(job.startedAt * 1000).toLocaleTimeString()} · {job.durationMs === null ? "running" : `${String(job.durationMs)} ms`} · exit {job.exitCode ?? "–"}</small>{job.output ? <div className="code-shell"><CopyButton text={job.output} label="Git-Output kopieren" /><pre>{job.output}</pre></div> : null}{job.error ? <p className="inline-error">{job.error}</p> : null}</article>)}</div></details>;
}

function deriveName(url: string): string { return url.split(/[/:]/).at(-1)?.replace(/\.git$/, "") ?? ""; }
function canSubmit(action: RepositoryAction, values: Record<string, unknown>): boolean {
  if (action === "create" || action === "clone" || action === "rename") return Boolean(values.name) && (action !== "clone" || Boolean(values.url));
  if (action === "commit") return Boolean(values.message) && (values.selected as string[]).length > 0 && !(values.repository as Repository | null)?.status.conflicts;
  if (action === "branches") return Boolean(values.branch);
  if (action === "publish") return Boolean(values.github) && Boolean(values.name) && (values.visibility !== "public" || values.confirmation === values.name);
  return true;
}
