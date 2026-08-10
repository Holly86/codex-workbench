import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useRef, useState } from "react";

import { AnchoredMenu } from "./AnchoredMenu";

function MenuHarness() {
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLButtonElement>(null);
  return (
    <>
      <button ref={anchorRef} type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>Aktionen</button>
      <AnchoredMenu anchorRef={anchorRef} open={open} onClose={() => setOpen(false)} ariaLabel="Testaktionen">
        <button type="button" role="menuitem">Erste Aktion</button>
        <button type="button" role="menuitem">Zweite Aktion</button>
      </AnchoredMenu>
    </>
  );
}

test("supports arrow navigation and restores focus when Escape closes the menu", async () => {
  render(<MenuHarness />);
  const anchor = screen.getByRole("button", { name: "Aktionen" });
  fireEvent.click(anchor);

  const first = await screen.findByRole("menuitem", { name: "Erste Aktion" });
  const second = screen.getByRole("menuitem", { name: "Zweite Aktion" });
  await waitFor(() => expect(first).toHaveFocus());
  fireEvent.keyDown(document, { key: "ArrowDown" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("menu", { name: "Testaktionen" })).not.toBeInTheDocument();
  expect(anchor).toHaveFocus();
});
