export interface WorkbenchRoute {
  repositoryId: string | null;
  threadId: string | null;
}

const ID = /^[A-Za-z0-9_-]{1,200}$/;

export function parseWorkbenchRoute(pathname: string): WorkbenchRoute {
  const parts = pathname.split("/").filter(Boolean).map((part) => {
    try { return decodeURIComponent(part); } catch { return ""; }
  });
  if (parts.length === 4 && parts[0] === "repositories" && parts[2] === "threads"
      && ID.test(parts[1] ?? "") && ID.test(parts[3] ?? "")) {
    return { repositoryId: parts[1] ?? null, threadId: parts[3] ?? null };
  }
  if (parts.length === 2 && parts[0] === "repositories" && ID.test(parts[1] ?? "")) {
    return { repositoryId: parts[1] ?? null, threadId: null };
  }
  return { repositoryId: null, threadId: null };
}

export function repositoryPath(repositoryId: string): string {
  return `/repositories/${encodeURIComponent(repositoryId)}`;
}

export function threadPath(repositoryId: string, threadId: string): string {
  return `${repositoryPath(repositoryId)}/threads/${encodeURIComponent(threadId)}`;
}

export function writeWorkbenchRoute(path: string, replace = false): void {
  if (window.location.pathname === path) return;
  window.history[replace ? "replaceState" : "pushState"]({}, "", path);
}
