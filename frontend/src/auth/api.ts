export type AuthState =
  | { state: "setup_required"; csrfToken: null; expiresAt: null; reason: null }
  | {
      state: "unauthenticated";
      csrfToken: null;
      expiresAt: null;
      reason: "session_expired" | null;
    }
  | {
      state: "authenticated";
      csrfToken: string;
      expiresAt: number;
      reason: null;
    };

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, code: string, message: string, retryAfterSeconds: number | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseAuthState(value: unknown): AuthState {
  if (!isRecord(value) || typeof value.state !== "string") {
    throw new ApiError(502, "invalid_response", "Ungültige Serverantwort.", null);
  }
  if (value.state === "setup_required") {
    return { state: "setup_required", csrfToken: null, expiresAt: null, reason: null };
  }
  if (value.state === "unauthenticated") {
    const reason = value.reason === "session_expired" ? value.reason : null;
    return { state: "unauthenticated", csrfToken: null, expiresAt: null, reason };
  }
  if (
    value.state === "authenticated" &&
    typeof value.csrfToken === "string" &&
    typeof value.expiresAt === "number"
  ) {
    return {
      state: "authenticated",
      csrfToken: value.csrfToken,
      expiresAt: value.expiresAt,
      reason: null,
    };
  }
  throw new ApiError(502, "invalid_response", "Ungültige Serverantwort.", null);
}

async function parseError(response: Response): Promise<ApiError> {
  let code = "request_failed";
  let message = "Die Anfrage ist fehlgeschlagen.";
  try {
    const body: unknown = await response.json();
    if (isRecord(body) && isRecord(body.error)) {
      if (typeof body.error.code === "string") {
        code = body.error.code;
      }
      if (typeof body.error.message === "string") {
        message = body.error.message;
      }
    }
  } catch {
    // A safe generic error remains authoritative for malformed error payloads.
  }
  const retryAfterHeader = response.headers.get("retry-after");
  const parsedRetryAfter = retryAfterHeader === null ? Number.NaN : Number(retryAfterHeader);
  return new ApiError(
    response.status,
    code,
    message,
    Number.isFinite(parsedRetryAfter) ? parsedRetryAfter : null,
  );
}

async function requestAuthState(
  path: string,
  init: RequestInit,
  signal?: AbortSignal,
): Promise<AuthState> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    signal,
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return parseAuthState(await response.json());
}

export function getAuthStatus(signal?: AbortSignal): Promise<AuthState> {
  return requestAuthState("/api/auth/status", { method: "GET" }, signal);
}

export function setupAccount(password: string): Promise<AuthState> {
  return requestAuthState("/api/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

export function login(password: string): Promise<AuthState> {
  return requestAuthState("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
}

export function logout(csrfToken: string): Promise<AuthState> {
  return requestAuthState("/api/auth/logout", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrfToken,
    },
    body: "{}",
  });
}
