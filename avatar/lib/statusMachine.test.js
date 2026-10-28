import { describe, it, expect } from "vitest";
import { canApplyStatus, STICKY_STATES } from "./statusMachine.js";

describe("STICKY_STATES", () => {
  it("contains fatal and mic_error, and NOT asset_error", () => {
    expect(STICKY_STATES.has("fatal")).toBe(true);
    expect(STICKY_STATES.has("mic_error")).toBe(true);
    // asset_error is a non-blocking notice: audio + captions survive a missing VRM.
    expect(STICKY_STATES.has("asset_error")).toBe(false);
  });
});

describe("canApplyStatus", () => {
  it("blocks a routine status from overwriting a sticky state", () => {
    // fatal (sticky) must NOT be hidden by a routine "connected".
    expect(canApplyStatus("fatal", "connected")).toBe(false);
    expect(canApplyStatus("mic_error", "listening")).toBe(false);
  });

  it("allows 'speaking' to clear a sticky state (Norivy recovered)", () => {
    expect(canApplyStatus("fatal", "speaking")).toBe(true);
    expect(canApplyStatus("mic_error", "speaking")).toBe(true);
  });

  it("allows one sticky state to overwrite another sticky state", () => {
    expect(canApplyStatus("mic_error", "fatal")).toBe(true);
    expect(canApplyStatus("fatal", "mic_error")).toBe(true);
  });

  it("allows any transition when the current state is not sticky", () => {
    expect(canApplyStatus("connected", "listening")).toBe(true);
    expect(canApplyStatus("offline", "connected")).toBe(true);
    // asset_error is NOT sticky → it is overwritten by a later good status.
    expect(canApplyStatus("asset_error", "connected")).toBe(true);
  });

  it("allows a routine status to overwrite asset_error (non-sticky notice)", () => {
    expect(canApplyStatus("asset_error", "speaking")).toBe(true);
    expect(canApplyStatus("asset_error", "listening")).toBe(true);
  });

  it("honors a custom stickyStates set", () => {
    const custom = new Set(["frozen"]);
    expect(canApplyStatus("frozen", "connected", custom)).toBe(false);
    expect(canApplyStatus("fatal", "connected", custom)).toBe(true); // fatal not sticky here
  });
});
