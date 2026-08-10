import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Conversation } from "./Conversation";
import type { ThreadView } from "../workbench/types";

it("copies assistant plain text and fenced code independently", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText } });
  const thread = { id: "t", name: null, preview: "p", turns: [{ id: "turn", status: "completed", items: [{ id: "a", type: "agentMessage", text: "Hello **world**\n```ts\nconst π = 3;\n```" }] }] } as unknown as ThreadView;
  render(<Conversation thread={thread} />);
  fireEvent.click(screen.getByRole("button", { name: "Code kopieren" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("const π = 3;\n"));
  fireEvent.click(screen.getByRole("button", { name: "Assistant-Antwort kopieren" }));
  await waitFor(() => expect(writeText).toHaveBeenLastCalledWith("Hello world\nconst π = 3;\n"));
});
