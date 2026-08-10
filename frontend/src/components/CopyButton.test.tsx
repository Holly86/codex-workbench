import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { CopyButton } from "./CopyButton";

describe("CopyButton", () => {
  it("copies unicode text by keyboard-accessible button and confirms success", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
    render(<CopyButton text="Grüße 世界" />);
    fireEvent.click(screen.getByRole("button", { name: "In Zwischenablage kopieren" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("Grüße 世界"));
    expect(screen.getByRole("button", { name: "Kopiert" })).toBeInTheDocument();
  });

  it("reports clipboard permission failures without throwing", async () => {
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: {
      writeText: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")),
    } });
    render(<CopyButton text="secret-free output" />);
    fireEvent.click(screen.getByRole("button"));
    expect(await screen.findByRole("button", { name: "Kopieren fehlgeschlagen" })).toBeInTheDocument();
  });
});
