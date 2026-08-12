import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type { NativeJob } from "../workbench/types";
import { NotificationCenter } from "./NotificationCenter";

const failed: NativeJob = {
  id: "job_opaque", repositoryId: "0123456789abcdef", repositoryName: "Beispiel",
  type: "git_pull", status: "timed_out", startedAt: 1, endedAt: 2, exitCode: null,
  error: "Der Vorgang hat das Zeitlimit überschritten.", errorCode: "git_operation_timeout",
  retryable: true, operationLabel: "Fast-Forward Pull", durationMs: 30_000,
};

beforeEach(() => localStorage.clear());

test("filters, retries, and persists opaque dismissal across remount", () => {
  const retry = vi.fn(() => Promise.resolve());
  const first = render(<NotificationCenter open jobs={[failed]} onClose={() => undefined} onRetry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "Fehler" }));
  fireEvent.click(screen.getByRole("button", { name: "Sicher erneut versuchen" }));
  expect(retry).toHaveBeenCalledWith(failed);
  fireEvent.click(screen.getByRole("button", { name: "Ausblenden" }));
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
  first.unmount();
  render(<NotificationCenter open jobs={[failed]} onClose={() => undefined} onRetry={retry} />);
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
  expect(localStorage.getItem("codex-workbench.ui.v1")).not.toContain("Beispiel");
});

test("closes with Escape", () => {
  const close = vi.fn();
  render(<NotificationCenter open jobs={[]} onClose={close} onRetry={() => Promise.resolve()} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});

test("shows recovery notices and dismisses every completed job in one action", () => {
  const succeeded = { ...failed, id: "job_done", status: "succeeded" as const,
    error: null, errorCode: null, retryable: false };
  render(<NotificationCenter open jobs={[failed, succeeded]} processConnection="reconnecting"
    realtimeConnection="disconnected" generation={4} onClose={() => undefined}
    onRetry={() => Promise.resolve()} />);
  expect(screen.getByText("Codex Runtime wird wiederhergestellt")).toBeInTheDocument();
  expect(screen.getByText("Live-Verbindung ist getrennt")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Alle erledigten ausblenden" }));
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
});
