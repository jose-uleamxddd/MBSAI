// statusMachine.js — Pure guard for the status pill transitions.
//
// Extracted from renderer.js `setStatus`. Pure: no DOM. A sticky critical state
// (fatal / mic_error) must stay visible to the operator until Norivy really
// recovers — it is only overwritten by another sticky state OR by "speaking"
// (she spoke again). asset_error is intentionally NOT sticky: audio + captions
// survive a missing VRM, so a later good status may clear the pill.

/**
 * Critical states that a routine status must not overwrite.
 * @type {Set<string>}
 */
export const STICKY_STATES = new Set(["fatal", "mic_error"]);

// The one non-sticky state allowed to clear a sticky pill: she spoke again.
const RECOVERY_STATE = "speaking";

/**
 * Decide whether a status transition may be applied to the pill.
 *
 * @param {string} currentState - the pill's current state key.
 * @param {string} nextState - the incoming state key.
 * @param {Set<string>} [stickyStates=STICKY_STATES] - states that resist routine overwrite.
 * @returns {boolean} true if the transition should be applied; false if the
 *   current sticky state must be preserved.
 */
export function canApplyStatus(currentState, nextState, stickyStates = STICKY_STATES) {
  // A sticky state is only overwritten by another sticky OR by "speaking" —
  // never by a routine "connected"/"listening" that would hide the problem.
  if (
    stickyStates.has(currentState) &&
    !stickyStates.has(nextState) &&
    nextState !== RECOVERY_STATE
  ) {
    return false;
  }
  return true;
}
