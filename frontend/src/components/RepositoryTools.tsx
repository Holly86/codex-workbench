import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { BranchTarget, GitHubCapability, Repository, RepositoryRemovalPreview } from "../workbench/types";
import type { RepositoryAction } from "./RepositorySidebar";
import {
  CheckboxRow,
  ChoiceCard,
  DangerButton,
  DefinitionList,
  DialogShell,
  FormField,
  InlineAlert,
  PrimaryButton,
  SecondaryButton,
  StatusBadge,
} from "./ui";

interface BranchInventory {
  local: BranchTarget[];
  remote: BranchTarget[];
}

interface RepositoryToolsProps {
  action: RepositoryAction | null;
  repository: Repository | null;
  github: GitHubCapability | null;
  pending: boolean;
  removalPreview: RepositoryRemovalPreview | null;
  onClose: () => void;
  onSubmit: (values: Record<string, unknown>) => Promise<void>;
  onLoadBranches?: () => Promise<BranchInventory>;
}

export function RepositoryTools(props: RepositoryToolsProps) {
  if (props.action === null) return null;
  const key = `${props.action}:${props.repository?.id ?? "global"}`;
  return <RepositoryDialog key={key} {...props} action={props.action} />;
}

function RepositoryDialog({ action, repository, github, pending, removalPreview, onClose, onSubmit, onLoadBranches }: RepositoryToolsProps & { action: RepositoryAction }) {
  const [name, setName] = useState(repository?.name ?? "");
  const [url, setUrl] = useState("");
  const [message, setMessage] = useState("");
  const [branch, setBranch] = useState("");
  const [branchTargetId, setBranchTargetId] = useState("");
  const [branchQuery, setBranchQuery] = useState("");
  const [branches, setBranches] = useState<BranchInventory | null>(null);
  const [branchLoading, setBranchLoading] = useState(false);
  const [branchError, setBranchError] = useState<string | null>(null);
  const [branchOperation, setBranchOperation] = useState<"create" | "switch">("create");
  const [selected, setSelected] = useState<string[]>([]);
  const [visibility, setVisibility] = useState<"private" | "public">("private");
  const [confirmation, setConfirmation] = useState("");
  const [readme, setReadme] = useState(true);
  const [deleteThreads, setDeleteThreads] = useState(false);
  const title = dialogTitle(action);
  const branchLoaderRef = useRef(onLoadBranches);
  useEffect(() => { branchLoaderRef.current = onLoadBranches; }, [onLoadBranches]);
  const loadBranchInventory = useCallback(async () => {
    const loader = branchLoaderRef.current;
    if (loader === undefined) return;
    setBranchLoading(true);
    setBranchError(null);
    try { setBranches(await loader()); }
    catch { setBranchError("Branches konnten nicht geladen werden."); }
    finally { setBranchLoading(false); }
  }, []);
  useEffect(() => {
    if (action === "branches") void loadBranchInventory();
  }, [action, loadBranchInventory]);
  const selectedBranch = branches === null ? undefined
    : [...branches.local, ...branches.remote].find((item) => item.id === branchTargetId);
  const submittedBranch = branchOperation === "switch" ? selectedBranch?.switchTarget ?? "" : branch;
  const values = { name, url, message, branch: submittedBranch, selected, visibility, confirmation, repository, github, removalPreview };
  const allowed = canSubmit(action, values);
  const buttonLabel = action === "branches"
    ? branchOperation === "create" ? "Branch erstellen" : "Branch wechseln"
    : submitLabel(action);
  return <DialogShell
    open
    title={title}
    description={dialogDescription(action)}
    tone={action === "delete" ? "danger" : "default"}
    onClose={onClose}
    footer={<><SecondaryButton onClick={onClose}>Schließen</SecondaryButton>{buttonLabel ? action === "delete" ? <DangerButton disabled={pending || !allowed} onClick={() => void onSubmit({ name, url, message, branch: submittedBranch, branchOperation, selected, visibility, confirmation, readme, deleteThreads })}>{pending ? "Wird ausgeführt …" : buttonLabel}</DangerButton> : <PrimaryButton disabled={pending || !allowed} onClick={() => void onSubmit({ name, url, message, branch: submittedBranch, branchOperation, selected, visibility, confirmation, readme, deleteThreads })}>{pending ? "Wird ausgeführt …" : buttonLabel}</PrimaryButton> : null}</>}
  >
    {action === "create" ? <CreateRepository name={name} readme={readme} onName={setName} onReadme={setReadme} /> : null}
    {action === "clone" ? <CloneRepository name={name} url={url} onName={setName} onUrl={(value) => { setUrl(value); if (!name) setName(deriveName(value)); }} /> : null}
    {action === "commit" ? <CommitChanges repository={repository} selected={selected} message={message} onSelected={setSelected} onMessage={setMessage} /> : null}
    {action === "branches" ? <Branches repository={repository} operation={branchOperation} branch={branch} targetId={branchTargetId} query={branchQuery} inventory={branches} loading={branchLoading} error={branchError} onRetry={() => void loadBranchInventory()} onOperation={setBranchOperation} onBranch={setBranch} onTarget={setBranchTargetId} onQuery={setBranchQuery} /> : null}
    {action === "rename" ? <FormField label="Anzeigename" description="Verzeichnis und Remote-Repository werden nicht umbenannt."><input value={name} onChange={(event) => setName(event.target.value)} /></FormField> : null}
    {action === "publish" ? <Publish github={github} name={name} visibility={visibility} confirmation={confirmation} onName={setName} onVisibility={setVisibility} onConfirmation={setConfirmation} /> : null}
    {action === "status" || action === "changes" ? <GitStatusView repository={repository} onAction={(quickAction) => onSubmit({ quickAction })} /> : null}
    {action === "remote" ? <DefinitionList values={[{ label: "Origin", value: github?.remoteUrl ?? "Kein origin-Remote konfiguriert" }, { label: "GitHub", value: github?.authenticated ? "Authentifiziert" : "Nicht authentifiziert" }]} /> : null}
    {action === "settings" ? <InlineAlert>Repository-Einstellungen bleiben an die serverseitig geprüfte Repository-ID gebunden.</InlineAlert> : null}
    {action === "remove" || action === "delete" ? <Removal action={action} preview={removalPreview} confirmation={confirmation} deleteThreads={deleteThreads} onConfirmation={setConfirmation} onDeleteThreads={setDeleteThreads} /> : null}
  </DialogShell>;
}

function CreateRepository({ name, readme, onName, onReadme }: { name: string; readme: boolean; onName: (value: string) => void; onReadme: (value: boolean) => void }) {
  return <><FormField label="Repositoryname" description="Das Repository wird als direkter Workspace-Unterordner mit dem Standardbranch main angelegt."><input value={name} onChange={(event) => onName(event.target.value)} /></FormField><CheckboxRow checked={readme} label="README-Datei erstellen" onChange={onReadme} /></>;
}
function CloneRepository({ name, url, onName, onUrl }: { name: string; url: string; onName: (value: string) => void; onUrl: (value: string) => void }) {
  return <><FormField label="Repository-URL" description="Öffentliche HTTPS-Repositories werden vorab geprüft. Private Repositories verwenden den Deploy-Key-Workflow."><input value={url} onChange={(event) => onUrl(event.target.value)} placeholder="https://github.com/organisation/repository.git" /></FormField><FormField label="Lokaler Name"><input value={name} onChange={(event) => onName(event.target.value)} /></FormField></>;
}

function CommitChanges({ repository, selected, message, onSelected, onMessage }: { repository: Repository | null; selected: string[]; message: string; onSelected: (value: string[]) => void; onMessage: (value: string) => void }) {
  const changes = repository?.status.changes ?? [];
  const all = changes.length > 0 && selected.length === changes.length;
  const toggleAll = (checked: boolean) => onSelected(checked ? changes.map((change) => change.path) : []);
  return <>{repository?.status.conflicts ? <InlineAlert tone="danger">Konflikte müssen vor einem Commit aufgelöst werden.</InlineAlert> : null}<div className="change-picker"><SelectAllCheckbox checked={all} indeterminate={selected.length > 0 && !all} onChange={toggleAll} />{changes.map((change) => <CheckboxRow key={change.path} checked={selected.includes(change.path)} label={`${change.path} · ${changeLabel(change.kind, change.staged)}`} onChange={(checked) => onSelected(checked ? [...selected, change.path] : selected.filter((item) => item !== change.path))} />)}</div><FormField label="Commit-Nachricht" description="Beschreibe knapp, warum diese zusammengehörigen Änderungen nötig sind."><textarea value={message} onChange={(event) => onMessage(event.target.value)} /></FormField></>;
}
function SelectAllCheckbox({ checked, indeterminate, onChange }: { checked: boolean; indeterminate: boolean; onChange: (value: boolean) => void }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = indeterminate; }, [indeterminate]);
  return <label className="checkbox-row"><input ref={ref} type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} /><strong>Alle Änderungen auswählen</strong></label>;
}

function Branches({ repository, operation, branch, targetId, query, inventory, loading, error, onRetry, onOperation, onBranch, onTarget, onQuery }: { repository: Repository | null; operation: "create" | "switch"; branch: string; targetId: string; query: string; inventory: BranchInventory | null; loading: boolean; error: string | null; onRetry: () => void; onOperation: (value: "create" | "switch") => void; onBranch: (value: string) => void; onTarget: (value: string) => void; onQuery: (value: string) => void }) {
  const normalized = query.trim().toLocaleLowerCase();
  const filtered = useMemo(() => ({
    local: inventory?.local.filter((item) => `${item.displayName} ${item.fullRef}`.toLocaleLowerCase().includes(normalized)) ?? [],
    remote: inventory?.remote.filter((item) => `${item.displayName} ${item.fullRef}`.toLocaleLowerCase().includes(normalized)) ?? [],
  }), [inventory, normalized]);
  const validationError = branch && !validBranch(branch) ? "Erlaubt sind Git-Ref-Zeichen ohne Leerzeichen, '..', '@{' oder führende Trennzeichen." : null;
  return <><DefinitionList values={[{ label: "Aktueller Branch", value: repository?.status.detached ? "Detached HEAD – keinem lokalen Branch zugeordnet" : repository?.status.branch ?? "Kein Branch" }]} /><div className="choice-grid"><ChoiceCard checked={operation === "create"} label="Neuen lokalen Branch erstellen" description="Erstellt den Branch am aktuellen Commit und wechselt direkt dorthin." onChange={() => { onOperation("create"); onBranch(""); onTarget(""); }} /><ChoiceCard checked={operation === "switch"} label="Zu vorhandenem Branch wechseln" description="Zeigt lokale und bekannte Remote-Branches mit eindeutiger Git-Identität an." onChange={() => { onOperation("switch"); onBranch(""); onTarget(""); }} /></div>{operation === "create" ? <FormField label="Branchname" error={validationError}><input value={branch} onChange={(event) => onBranch(event.target.value)} placeholder="feature/beispiel" /></FormField> : <>{loading ? <p role="status">Branches werden geladen …</p> : null}{error ? <InlineAlert tone="danger">{error}<button type="button" onClick={onRetry}>Erneut versuchen</button></InlineAlert> : null}{!loading && error === null && inventory ? <><FormField label="Branches durchsuchen"><input type="search" value={query} onChange={(event) => onQuery(event.target.value)} /></FormField><BranchGroup title="Lokale Branches" branches={filtered.local} selected={targetId} onSelect={onTarget} /><BranchGroup title="Remote-Branches" branches={filtered.remote} selected={targetId} onSelect={onTarget} /></> : null}</>}</>;
}
function BranchGroup({ title, branches, selected, onSelect }: { title: string; branches: BranchTarget[]; selected: string; onSelect: (value: string) => void }) { return <section className="branch-group"><h3>{title}</h3>{branches.length === 0 ? <p>Keine passenden Branches.</p> : <ul>{branches.map((item) => <li key={item.id}><label title={item.fullRef}><input type="radio" name="branch-target" checked={selected === item.id} disabled={item.disabledReason !== null} onChange={() => onSelect(item.id)} /><span>{item.displayName}{item.current ? " · aktuell" : ""}<small>{item.fullRef}</small></span></label></li>)}</ul>}</section>; }

function GitStatusView({ repository, onAction }: { repository: Repository | null; onAction: (action: string) => Promise<void> }) {
  if (!repository) return <p>Kein Repository gewählt.</p>;
  const status = repository.status;
  const groups = (["conflict", "modified", "untracked", "staged"] as const).map((group) => ({ group, changes: status.changes.filter((change) => group === "staged" ? change.staged : group === "conflict" ? change.kind === "conflict" : !change.staged && change.kind === group) })).filter((group) => group.changes.length > 0);
  return <><div className="git-status-summary"><DefinitionList values={[{ label: "Repository", value: repository.name }, { label: "Branch", value: status.detached ? "Detached HEAD" : status.branch ?? "Kein Branch" }, { label: "Upstream", value: status.upstreamMissing ? "Nicht eingerichtet" : status.upstream ?? "Nicht eingerichtet" }, { label: "Synchronisation", value: syncLabel(status) }, { label: "Arbeitsbaum", value: status.clean ? "Clean – keine lokalen Änderungen" : `${String(status.staged)} staged · ${String(status.modified)} geändert · ${String(status.untracked)} untracked · ${String(status.conflicts)} Konflikte` }, { label: "Beobachtet", value: new Date(status.observedAt * 1000).toLocaleString() }]} /><StatusBadge state={status.conflicts ? "failed" : status.clean ? "healthy" : "degraded"}>{status.conflicts ? "Konflikt" : status.clean ? "Clean" : "Änderungen"}</StatusBadge></div>{groups.map(({ group, changes }) => <section className="status-change-group" key={group}><h3>{groupLabel(group)}</h3><ul>{changes.map((change) => <li key={`${group}:${change.path}`}><code>{change.path}</code><span>{changeLabel(change.kind, change.staged)}</span></li>)}</ul></section>)}<InlineAlert tone={status.conflicts ? "danger" : "info"}>{status.conflicts ? "Konflikte müssen lokal aufgelöst werden, bevor Commit oder Pull sicher fortgesetzt werden kann." : status.clean ? "Clean bedeutet: Git kennt keine lokalen Dateiänderungen. Ahead/behind vergleicht den Branch mit seinem Upstream." : "Prüfe die Änderungen und erstelle einen Commit. Fetch ist rein abrufend; Pull verwendet ausschließlich Fast-forward."}</InlineAlert><div className="git-status-actions"><button type="button" onClick={() => void onAction("changes")}>Änderungen anzeigen</button><button type="button" disabled={status.conflicts > 0 || status.clean} onClick={() => void onAction("commit")}>Commit öffnen</button><button type="button" onClick={() => void onAction("fetch")}>Fetch</button><button type="button" disabled={status.conflicts > 0 || status.behind === 0} onClick={() => void onAction("pull")}>Pull – nur Fast-forward</button><button type="button" disabled={status.ahead === 0 || status.upstreamMissing} onClick={() => void onAction("push")}>Push</button></div><details className="technical-details"><summary>Technische Details</summary><pre>{JSON.stringify(status, null, 2)}</pre></details></>;
}

function Publish({ github, name, visibility, confirmation, onName, onVisibility, onConfirmation }: { github: GitHubCapability | null; name: string; visibility: "private" | "public"; confirmation: string; onName: (value: string) => void; onVisibility: (value: "private" | "public") => void; onConfirmation: (value: string) => void }) { return github?.authenticated ? <><DefinitionList values={[{ label: "GitHub-Konto", value: github.account ?? "Unbekannt" }]} /><FormField label="Repositoryname"><input value={name} onChange={(event) => onName(event.target.value)} /></FormField><div className="choice-grid"><ChoiceCard checked={visibility === "private"} label="Privat" description="Nur berechtigte GitHub-Nutzer können das Repository sehen." onChange={() => onVisibility("private")} /><ChoiceCard checked={visibility === "public"} label="Öffentlich" description="Quellcode und Historie werden öffentlich im Internet sichtbar." onChange={() => onVisibility("public")} /></div>{visibility === "public" ? <InlineAlert tone="warning"><strong>Öffentliche Veröffentlichung</strong><FormField label="Repositorynamen zur Bestätigung eingeben"><input value={confirmation} onChange={(event) => onConfirmation(event.target.value)} /></FormField></InlineAlert> : null}</> : <InlineAlert tone="warning">GitHub CLI ist auf diesem Workbench-Host nicht authentifiziert.</InlineAlert>; }
function Removal({ action, preview, confirmation, deleteThreads, onConfirmation, onDeleteThreads }: { action: "remove" | "delete"; preview: RepositoryRemovalPreview | null; confirmation: string; deleteThreads: boolean; onConfirmation: (value: string) => void; onDeleteThreads: (value: boolean) => void }) { return <div className="repository-removal"><InlineAlert tone={action === "delete" ? "danger" : "info"}>{action === "remove" ? "Nur der Workbench-Eintrag wird entfernt. Das lokale Repository bleibt erhalten." : "Das lokale Repository-Verzeichnis wird unwiderruflich gelöscht. Remotes bleiben unverändert."}</InlineAlert>{preview === null ? <p role="status">Sicherheitsprüfung läuft …</p> : <><DefinitionList values={[{ label: "Repository", value: preview.repositoryName }, { label: "Sicheres Ziel", value: <code>{preview.relativePath}</code> }, { label: "Zugeordnete Threads", value: preview.threadCount }, { label: "Lokale Änderungen", value: preview.hasUncommittedChanges ? "Vorhanden" : "Keine" }]} />{preview.blockedReasons.length ? <InlineAlert tone="danger">Aktive Turns, Jobs oder ein unbekannter Threadzustand blockieren die Aktion.</InlineAlert> : null}<CheckboxRow checked={deleteThreads} label="Zugeordnete Threads ebenfalls löschen (standardmäßig aus)" onChange={onDeleteThreads} /><FormField label="Exakten Repositorynamen zur Bestätigung eingeben"><input value={confirmation} onChange={(event) => onConfirmation(event.target.value)} autoComplete="off" /></FormField></>}</div>; }

function deriveName(url: string): string { return url.split(/[/:]/).at(-1)?.replace(/\.git$/, "") ?? ""; }
function validBranch(value: string): boolean { return /^(?![-./])(?!.*(?:\.\.|\/\/|@\{|\\))[A-Za-z0-9._/-]{1,200}(?<![./])$/.test(value); }
function dialogTitle(action: RepositoryAction): string { return ({ create: "Repository erstellen", clone: "Repository klonen", commit: "Änderungen committen", branches: "Branches verwalten", rename: "Anzeigenamen ändern", publish: "Auf GitHub veröffentlichen", status: "Git-Status", changes: "Änderungen", remote: "Remote & Clone", settings: "Repository-Einstellungen", remove: "Aus Workbench entfernen", delete: "Lokales Repository löschen" } as Partial<Record<RepositoryAction, string>>)[action] ?? "Repository"; }
function dialogDescription(action: RepositoryAction): string { return ({ status: "Strukturierter Zustand von Branch, Upstream und Arbeitsbaum.", branches: "Lokale Branches erstellen oder sicher zu einem vorhandenen Branch wechseln.", commit: "Wähle zusammengehörige Änderungen und beschreibe ihren Zweck.", remove: "Das Repository bleibt auf dem Host bestehen.", delete: "Diese Aktion verändert nur den lokalen Workbench-Host." } as Partial<Record<RepositoryAction, string>>)[action] ?? "Repository-Aktion sicher ausführen."; }
function submitLabel(action: RepositoryAction): string | null { return ({ create: "Repository erstellen", clone: "Repository klonen", commit: "Commit erstellen", branches: "Branch erstellen", rename: "Anzeigename speichern", publish: "Repository veröffentlichen", remove: "Aus Workbench entfernen", delete: "Lokal löschen" } as Partial<Record<RepositoryAction, string>>)[action] ?? null; }
function syncLabel(status: Repository["status"]): string { if (status.upstreamMissing) return "Nur lokal – Upstream fehlt"; if (status.ahead && status.behind) return `${String(status.ahead)} voraus und ${String(status.behind)} zurück`; if (status.ahead) return `${String(status.ahead)} Commit(s) nur lokal`; if (status.behind) return `${String(status.behind)} Commit(s) nur remote`; return "Mit Upstream synchron"; }
function groupLabel(group: string): string { return ({ conflict: "Konflikte", staged: "Für Commit vorgemerkt", modified: "Geändert", untracked: "Nicht verfolgt" } as Record<string, string>)[group] ?? group; }
function changeLabel(kind: string, staged: boolean): string { if (kind === "conflict") return "Konflikt"; if (kind === "untracked") return "Neue, nicht verfolgte Datei"; return staged ? "Für Commit vorgemerkt" : kind === "deleted" ? "Gelöscht" : "Geändert"; }
function canSubmit(action: RepositoryAction, values: Record<string, unknown>): boolean { if (action === "create" || action === "clone" || action === "rename") return Boolean(values.name) && (action !== "clone" || Boolean(values.url)); if (action === "commit") return Boolean(values.message) && (values.selected as string[]).length > 0 && !(values.repository as Repository | null)?.status.conflicts; if (action === "branches") return typeof values.branch === "string" && validBranch(values.branch); if (action === "publish") return Boolean(values.github) && Boolean(values.name) && (values.visibility !== "public" || values.confirmation === values.name); if (action === "remove" || action === "delete") { const preview = values.removalPreview as RepositoryRemovalPreview | null; return preview !== null && preview.blockedReasons.length === 0 && values.confirmation === preview.repositoryName; } return true; }
