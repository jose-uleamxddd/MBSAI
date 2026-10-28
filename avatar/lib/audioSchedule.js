// audioSchedule.js — Pure gapless-playback scheduling math.
//
// Extracted from renderer.js `enqueueAudio`. Pure: no AudioContext, no window.
// Decides the `when` timestamp at which the next PCM16 chunk should start so the
// queue plays gapless: reuse the running `nextStartTime` if it is still ahead of
// `currentTime` by a minimum lead, otherwise fall back to `currentTime + gap`.

const DEFAULT_MIN_LEAD_S = 0.02;
const DEFAULT_GAP_S = 0.03;

/**
 * Compute the start time for the next scheduled audio chunk.
 *
 * @param {number} nextStartTime - the running cursor (end of the last chunk);
 *   0/null/undefined means "nothing scheduled".
 * @param {number} currentTime - the AudioContext's current time.
 * @param {{ minLeadS?: number, gapS?: number }} [options]
 * @param {number} [options.minLeadS=0.02] - minimum lead ahead of currentTime for
 *   `nextStartTime` to still be considered valid.
 * @param {number} [options.gapS=0.03] - gap added to currentTime on fallback.
 * @returns {number} the timestamp at which to start the chunk.
 */
export function computeStartTime(nextStartTime, currentTime, options = {}) {
  const minLeadS = options.minLeadS ?? DEFAULT_MIN_LEAD_S;
  const gapS = options.gapS ?? DEFAULT_GAP_S;
  let when = nextStartTime;
  if (!when || when < currentTime + minLeadS) when = currentTime + gapS;
  return when;
}
