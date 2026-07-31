import { fireEvent, render, screen } from "@testing-library/react";

import { Composer } from "./Composer";

test("enables sending only when the draft contains text", () => {
  render(
    <Composer
      csrfToken="csrf"
      disabled={false}
      working={false}
      onSend={() => Promise.resolve(true)}
      onStop={() => Promise.resolve()}
    />,
  );

  const input = screen.getByRole("textbox", { name: "Nachricht an Codex" });
  const submit = screen.getByRole("button", { name: "Senden" });

  expect(submit).toBeDisabled();
  fireEvent.change(input, { target: { value: "Prüfe den Git-Status" } });
  expect(submit).toBeEnabled();
  fireEvent.change(input, { target: { value: "   " } });
  expect(submit).toBeDisabled();
});
