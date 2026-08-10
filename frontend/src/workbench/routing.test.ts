import { describe, expect, it } from "vitest";
import { parseWorkbenchRoute, repositoryPath, threadPath } from "./routing";

describe("workbench routing", () => {
  it("parses canonical repository and thread routes", () => {
    expect(parseWorkbenchRoute("/repositories/0123456789abcdef/threads/thread_42"))
      .toEqual({ repositoryId: "0123456789abcdef", threadId: "thread_42" });
    expect(parseWorkbenchRoute("/repositories/0123456789abcdef"))
      .toEqual({ repositoryId: "0123456789abcdef", threadId: null });
  });

  it("rejects malformed and partial routes", () => {
    expect(parseWorkbenchRoute("/repositories/a/threads"))
      .toEqual({ repositoryId: null, threadId: null });
    expect(parseWorkbenchRoute("/repositories/%2F/threads/t"))
      .toEqual({ repositoryId: null, threadId: null });
  });

  it("builds encoded canonical paths", () => {
    expect(repositoryPath("repo_1")).toBe("/repositories/repo_1");
    expect(threadPath("repo_1", "thread-2")).toBe("/repositories/repo_1/threads/thread-2");
  });
});
