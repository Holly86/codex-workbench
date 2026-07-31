import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";

import {
  ApiError,
  getAuthStatus,
  login,
  logout,
  setupAccount,
  type AuthState,
} from "./api";

type BoundaryState =
  | { kind: "loading" }
  | { kind: "setup" }
  | { kind: "login"; expired: boolean }
  | { kind: "authenticated"; csrfToken: string; expiresAt: number }
  | { kind: "error"; message: string };

interface AuthBoundaryProps {
  children: (
    session: { csrfToken: string; expiresAt: number },
    performLogout: () => Promise<void>,
    logoutPending: boolean,
    expireSession: () => void,
  ) => ReactNode;
}

function focusOnMount(element: HTMLElement | null) {
  element?.focus();
}

function toBoundaryState(auth: AuthState): BoundaryState {
  if (auth.state === "setup_required") {
    return { kind: "setup" };
  }
  if (auth.state === "unauthenticated") {
    return { kind: "login", expired: auth.reason === "session_expired" };
  }
  return { kind: "authenticated", csrfToken: auth.csrfToken, expiresAt: auth.expiresAt };
}

function safeErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  return "Die Workbench ist momentan nicht erreichbar.";
}

export function AuthBoundary({ children }: AuthBoundaryProps) {
  const [state, setState] = useState<BoundaryState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [logoutPending, setLogoutPending] = useState(false);
  const [formMessage, setFormMessage] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void getAuthStatus(controller.signal)
      .then((auth) => {
        setState(toBoundaryState(auth));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setState({ kind: "error", message: safeErrorMessage(error) });
        }
      });
    return () => {
      controller.abort();
    };
  }, [reloadKey]);

  const submitPassword = async (password: string) => {
    if (state.kind !== "setup" && state.kind !== "login") {
      return;
    }
    setSubmitting(true);
    setFormMessage(null);
    try {
      const auth = state.kind === "setup" ? await setupAccount(password) : await login(password);
      setState(toBoundaryState(auth));
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 429) {
        const wait = error.retryAfterSeconds;
        setFormMessage(
          wait === null
            ? error.message
            : `${error.message} Erneut versuchen in ${Math.ceil(wait)} Sekunden.`,
        );
      } else {
        setFormMessage(safeErrorMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const performLogout = async () => {
    if (state.kind !== "authenticated") {
      return;
    }
    setLogoutPending(true);
    try {
      const auth = await logout(state.csrfToken);
      setState(toBoundaryState(auth));
      setFormMessage(null);
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 401) {
        setState({ kind: "login", expired: true });
      } else {
        setState({ kind: "error", message: safeErrorMessage(error) });
      }
    } finally {
      setLogoutPending(false);
    }
  };

  if (state.kind === "loading") {
    return (
      <AuthPage>
        <div className="auth-status" role="status" aria-live="polite">
          <span className="status-dot" aria-hidden="true" />
          Sitzung wird geprüft …
        </div>
      </AuthPage>
    );
  }

  if (state.kind === "error") {
    return (
      <AuthPage>
        <section className="auth-card" aria-labelledby="auth-error-heading">
          <p className="eyebrow">Sichere Verbindung</p>
          <h2 id="auth-error-heading">Workbench nicht verfügbar</h2>
          <p role="alert">{state.message}</p>
          <button
            type="button"
            className="primary-action"
            ref={focusOnMount}
            onClick={() => {
              setState({ kind: "loading" });
              setReloadKey((value) => value + 1);
            }}
          >
            Erneut versuchen
          </button>
        </section>
      </AuthPage>
    );
  }

  if (state.kind === "authenticated") {
    return children(state, performLogout, logoutPending, () => {
      setState({ kind: "login", expired: true });
    });
  }

  return (
    <AuthPage>
      <PasswordForm
        mode={state.kind}
        expired={state.kind === "login" && state.expired}
        submitting={submitting}
        message={formMessage}
        onSubmit={submitPassword}
      />
    </AuthPage>
  );
}

function AuthPage({ children }: { children: ReactNode }) {
  return (
    <main className="auth-page">
      <div className="auth-brand">
        <p className="eyebrow">Local agent workspace</p>
        <h1>Codex Workbench</h1>
      </div>
      {children}
    </main>
  );
}

interface PasswordFormProps {
  mode: "setup" | "login";
  expired: boolean;
  submitting: boolean;
  message: string | null;
  onSubmit: (password: string) => Promise<void>;
}

function PasswordForm({ mode, expired, submitting, message, onSubmit }: PasswordFormProps) {
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const passwordRef = useRef<HTMLInputElement>(null);
  const setupMode = mode === "setup";

  useEffect(() => {
    passwordRef.current?.focus();
  }, [mode]);

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (setupMode && password !== confirmation) {
      setLocalError("Die Passwörter stimmen nicht überein.");
      return;
    }
    setLocalError(null);
    const submittedPassword = password;
    setPassword("");
    setConfirmation("");
    void onSubmit(submittedPassword);
  };

  return (
    <section className="auth-card" aria-labelledby="auth-heading">
      <h2 id="auth-heading">{setupMode ? "Workbench einrichten" : "Anmelden"}</h2>
      <p>
        {setupMode
          ? "Lege das einzige Workbench-Passwort fest. Es wird ausschließlich als Argon2id-Prüfwert gespeichert."
          : "Melde dich mit deinem Workbench-Passwort an."}
      </p>
      {expired ? (
        <p className="auth-notice" role="status">
          Deine Sitzung ist abgelaufen. Bitte melde dich erneut an.
        </p>
      ) : null}
      <form onSubmit={handleSubmit} aria-label={setupMode ? "Workbench einrichten" : "Anmelden"}>
        <label htmlFor="auth-password">Passwort</label>
        <input
          id="auth-password"
          ref={passwordRef}
          type="password"
          value={password}
          minLength={setupMode ? 12 : 1}
          maxLength={1024}
          autoComplete={setupMode ? "new-password" : "current-password"}
          required
          disabled={submitting}
          onChange={(event) => {
            setPassword(event.target.value);
          }}
        />
        {setupMode ? (
          <>
            <label htmlFor="auth-password-confirmation">Passwort bestätigen</label>
            <input
              id="auth-password-confirmation"
              type="password"
              value={confirmation}
              minLength={12}
              maxLength={1024}
              autoComplete="new-password"
              required
              disabled={submitting}
              onChange={(event) => {
                setConfirmation(event.target.value);
              }}
            />
          </>
        ) : null}
        {localError ?? message ? (
          <p className="form-error" role="alert">
            {localError ?? message}
          </p>
        ) : null}
        <button type="submit" className="primary-action" disabled={submitting}>
          {submitting ? "Bitte warten …" : setupMode ? "Sicher einrichten" : "Anmelden"}
        </button>
      </form>
    </section>
  );
}
