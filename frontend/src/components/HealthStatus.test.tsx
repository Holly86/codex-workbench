import { render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { HealthStatus } from "./HealthStatus";

test("distinguishes an available backend from an unavailable Codex runtime", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 200 }))
      .mockResolvedValueOnce(new Response(null, { status: 503 })),
  );

  render(<HealthStatus processConnection="failed" />);

  expect(await screen.findByText("Backend verbunden")).toBeInTheDocument();
  expect(screen.getByText("Codex Runtime derzeit nicht verfügbar")).toBeInTheDocument();
  expect(screen.queryByText("Backend nicht erreichbar")).not.toBeInTheDocument();
});

test("keeps optional tool discovery out of global health", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));

  render(
    <HealthStatus processConnection="connected" />,
  );

  expect(await screen.findByText("Codex Runtime bereit")).toBeInTheDocument();
  expect(screen.queryByText(/Capabilities|Plugins/)).not.toBeInTheDocument();
});
