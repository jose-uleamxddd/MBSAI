import { describe, it, expect } from "vitest";
import { resolvePython } from "./pythonResolver.js";

const ROOT = "/proj";

describe("resolvePython", () => {
  it("respeta el override env.PYTHON por encima de todo", () => {
    const got = resolvePython({
      projectRoot: ROOT,
      env: { PYTHON: "/custom/py" },
      exists: () => true,
    });
    expect(got).toBe("/custom/py");
  });

  it("prefiere el python del .venv (unix) cuando existe", () => {
    const got = resolvePython({
      projectRoot: ROOT,
      platform: "darwin",
      env: {},
      exists: (p) => p === "/proj/.venv/bin/python",
    });
    expect(got).toBe("/proj/.venv/bin/python");
  });

  it("prefiere el python del .venv (win) cuando existe", () => {
    const got = resolvePython({
      projectRoot: "C:\\proj",
      platform: "win32",
      env: {},
      exists: () => true,
    });
    expect(got).toContain(".venv");
    expect(got).toContain("python.exe");
  });

  it("cae a python3 global en unix si no hay venv", () => {
    const got = resolvePython({
      projectRoot: ROOT,
      platform: "linux",
      env: {},
      exists: () => false,
    });
    expect(got).toBe("python3");
  });

  it("cae a python global en win si no hay venv", () => {
    const got = resolvePython({
      projectRoot: ROOT,
      platform: "win32",
      env: {},
      exists: () => false,
    });
    expect(got).toBe("python");
  });
});
