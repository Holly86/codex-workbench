import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";

import { App } from "./App";

const authenticated = {
  state: "authenticated",
  csrfToken: "csrf-test-token",
  expiresAt: 2_000_000_000,
  reason: null,
};

function jsonResponse(body: unknown, status = 200, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

function requestPath(input: RequestInfo | URL): string {
  if (typeof input === "string") {
    return input;
  }
  if (input instanceof URL) {
    return input.pathname;
  }
  return new URL(input.url).pathname;
}

test("renders the protected accessible shell after session validation", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === "/api/auth/status") {
        return jsonResponse(authenticated);
      }
      return new Response(null, { status: 200 });
    }),
  );

  render(<App />);

  expect(screen.getByRole("status")).toHaveTextContent("Sitzung wird geprüft");
  expect(await screen.findByRole("navigation", { name: "Repositories" })).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { level: 1, name: "Codex Workbench" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveAttribute("id", "conversation");
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus());
  expect(screen.getByRole("form", { name: "Nachricht verfassen" })).toBeInTheDocument();
  expect(await screen.findByText("Backend bereit")).toBeInTheDocument();
});

test("completes first-start setup and clears password fields", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const path = requestPath(input);
      if (path === "/api/auth/status") {
        return jsonResponse({ state: "setup_required" });
      }
      if (path === "/api/auth/setup") {
        return jsonResponse(authenticated);
      }
      return new Response(null, { status: 200 });
    }),
  );
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Workbench einrichten" })).toBeInTheDocument();
  const password = screen.getByLabelText("Passwort");
  const confirmation = screen.getByLabelText("Passwort bestätigen");
  await waitFor(() => expect(password).toHaveFocus());
  fireEvent.change(password, { target: { value: "correct horse battery staple" } });
  fireEvent.change(confirmation, { target: { value: "correct horse battery staple" } });
  fireEvent.submit(screen.getByRole("form", { name: "Workbench einrichten" }));

  expect(
    await screen.findByRole("form", { name: "Nachricht verfassen" }),
  ).toBeInTheDocument();
});

test("shows the session-expired login state with useful focus", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(
      jsonResponse({
        state: "unauthenticated",
        csrfToken: null,
        expiresAt: null,
        reason: "session_expired",
      }),
    ),
  );
  render(<App />);

  expect(await screen.findByText("Deine Sitzung ist abgelaufen. Bitte melde dich erneut an."))
    .toBeInTheDocument();
  await waitFor(() => expect(screen.getByLabelText("Passwort")).toHaveFocus());
});

test("renders login rate limiting without exposing submitted credentials", async () => {
  const submittedPassword = "a deliberately private password";
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      if (requestPath(input) === "/api/auth/status") {
        return jsonResponse({ state: "unauthenticated", reason: null });
      }
      return jsonResponse(
        {
          error: {
            code: "login_rate_limited",
            message: "Zu viele Anmeldeversuche. Bitte später erneut versuchen.",
          },
        },
        429,
        { "Retry-After": "42" },
      );
    }),
  );
  render(<App />);

  const password = await screen.findByLabelText("Passwort");
  fireEvent.change(password, { target: { value: submittedPassword } });
  fireEvent.submit(screen.getByRole("form", { name: "Anmelden" }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("Erneut versuchen in 42 Sekunden");
  expect(alert).not.toHaveTextContent(submittedPassword);
  expect(password).toHaveValue("");
});

test("shows a retryable backend error state", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("network details")));
  render(<App />);

  expect(await screen.findByRole("heading", { name: "Workbench nicht verfügbar" }))
    .toBeInTheDocument();
  const retry = screen.getByRole("button", { name: "Erneut versuchen" });
  expect(retry).toHaveFocus();
  expect(screen.queryByText("network details")).not.toBeInTheDocument();
});

test("logs out through the CSRF-authenticated API and returns to login", async () => {
  let logoutHeaders: HeadersInit | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const path = requestPath(input);
      if (path === "/api/auth/status") {
        return jsonResponse(authenticated);
      }
      if (path === "/api/auth/logout") {
        logoutHeaders = init?.headers;
        return jsonResponse({ state: "unauthenticated", reason: null });
      }
      return new Response(null, { status: 200 });
    }),
  );
  render(<App />);

  fireEvent.click(await screen.findByRole("button", { name: "Abmelden" }));
  expect(await screen.findByRole("heading", { name: "Anmelden" })).toBeInTheDocument();
  await waitFor(() => {
    expect(new Headers(logoutHeaders).get("x-csrf-token")).toBe("csrf-test-token");
  });
});
