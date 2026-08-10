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

  render(<HealthStatus processConnection="failed" capabilities={null} />);

  expect(await screen.findByText("Backend verbunden")).toBeInTheDocument();
  expect(screen.getByText("Codex Runtime derzeit nicht verfügbar")).toBeInTheDocument();
  expect(screen.queryByText("Backend nicht erreichbar")).not.toBeInTheDocument();
});

test("reports optional plugin degradation without hiding model availability", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 200 })));

  render(
    <HealthStatus
      processConnection="connected"
      capabilities={{
        overall: "partial",
        generation: 3,
        cached: true,
        stale: false,
        models: { available: true, reason: null },
        skills: { available: true, reason: null },
        plugins: { available: false, reason: "unsupported" },
      }}
    />,
  );

  expect(await screen.findByText("Plugins konnten nicht geladen werden")).toBeInTheDocument();
  expect(screen.getByText("Codex Runtime bereit")).toBeInTheDocument();
});
