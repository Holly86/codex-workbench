import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";
import type { ComponentProps } from "react";
import { beforeEach, expect, test, vi } from "vitest";

import type { NativeJob } from "../workbench/types";
import { readUiPreferences, writeUiPreferences } from "../workbench/uiPreferences";
import { NotificationCenter } from "./NotificationCenter";

function NotificationHarness(props: Omit<ComponentProps<typeof NotificationCenter>, "preferences" | "onPreferencesChange" | "recoveryEpisodes"> & { recoveryEpisodes?: ComponentProps<typeof NotificationCenter>["recoveryEpisodes"] }) {
  const [preferences, setPreferences] = useState(readUiPreferences);
  useEffect(() => writeUiPreferences(preferences), [preferences]);
  return <NotificationCenter {...props} recoveryEpisodes={props.recoveryEpisodes ?? []} preferences={preferences} onPreferencesChange={setPreferences} />;
}

const failed: NativeJob = {
  id: "job_opaque", repositoryId: "0123456789abcdef", repositoryName: "Beispiel",
  type: "git_pull", status: "timed_out", startedAt: 1, endedAt: 2, exitCode: null,
  error: "Der Vorgang hat das Zeitlimit überschritten.", errorCode: "git_operation_timeout",
  retryable: true, operationLabel: "Fast-Forward Pull", durationMs: 30_000,
};

beforeEach(() => localStorage.clear());

test("filters, retries, and persists opaque dismissal across remount", () => {
  const retry = vi.fn(() => Promise.resolve());
  const first = render(<NotificationHarness open jobs={[failed]} onClose={() => undefined} onRetry={retry} />);
  fireEvent.click(screen.getByRole("button", { name: "Fehler" }));
  fireEvent.click(screen.getByRole("button", { name: "Sicher erneut versuchen" }));
  expect(retry).toHaveBeenCalledWith(failed);
  fireEvent.click(screen.getByRole("button", { name: "Ausblenden" }));
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
  first.unmount();
  render(<NotificationHarness open jobs={[failed]} onClose={() => undefined} onRetry={retry} />);
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
  expect(localStorage.getItem("codex-workbench.ui.v2")).not.toContain("Beispiel");
});

test("closes with Escape", () => {
  const close = vi.fn();
  render(<NotificationHarness open jobs={[]} onClose={close} onRetry={() => Promise.resolve()} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(close).toHaveBeenCalledOnce();
});

test("shows recovery notices and dismisses every completed job in one action", () => {
  const succeeded = { ...failed, id: "job_done", status: "succeeded" as const,
    error: null, errorCode: null, retryable: false };
  render(<NotificationHarness open jobs={[failed, succeeded]} recoveryEpisodes={[
    { id: "recovery_000001", kind: "runtime", state: "recovering" },
    { id: "recovery_000002", kind: "realtime", state: "failed" },
  ]} onClose={() => undefined}
    onRetry={() => Promise.resolve()} />);
  expect(screen.getByText("Codex Runtime wird wiederhergestellt")).toBeInTheDocument();
  expect(screen.getByText("Live-Verbindung ist getrennt")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Alle erledigten ausblenden" }));
  expect(screen.queryByText("Fast-Forward Pull")).not.toBeInTheDocument();
});

test("does not refocus during 100 parent rerenders", async () => {
  const focus = vi.spyOn(HTMLButtonElement.prototype, "focus");
  function Parent({ tick }: { tick: number }) {
    void tick;
    return <NotificationCenter open jobs={[]} recoveryEpisodes={[]} preferences={readUiPreferences()}
      onPreferencesChange={() => undefined} onClose={() => undefined}
      onRetry={() => Promise.resolve()} />;
  }
  const view = render(<Parent tick={0} />);
  await waitFor(() => expect(focus).toHaveBeenCalledTimes(1));
  for (let index = 1; index <= 100; index += 1) view.rerender(<Parent tick={index} />);
  expect(focus).toHaveBeenCalledTimes(1);
  focus.mockRestore();
});
