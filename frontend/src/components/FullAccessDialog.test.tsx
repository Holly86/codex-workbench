import { fireEvent, render, screen } from "@testing-library/react";
import { vi } from "vitest";

import { FullAccessDialog } from "./FullAccessDialog";

test("requires an explicit acknowledgement and shows the exact full-access selection", () => {
  const confirm = vi.fn();
  render(
    <FullAccessDialog
      open
      repository="codex-workbench"
      approvalPolicy="never"
      pending={false}
      onConfirm={confirm}
      onCancel={vi.fn()}
    />,
  );

  expect(screen.getByText("codex-workbench")).toBeInTheDocument();
  expect(screen.getByText("danger-full-access")).toBeInTheDocument();
  expect(screen.getByText("never")).toBeInTheDocument();
  const activate = screen.getByRole("button", { name: "Full Access aktivieren" });
  expect(activate).toBeDisabled();
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(activate);
  expect(confirm).toHaveBeenCalledOnce();
});
