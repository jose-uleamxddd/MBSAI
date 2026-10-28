// lipsync.js — Pure amplitude → mouth-openness math for VRM lipsync.
//
// Extracted from renderer.js `updateLipsync`. Pure: no three.js, no analyser.
// Maps an RMS amplitude to the VRM "aa" expression value in [0, 1], applying the
// configured gain (rms is small) then exponentially smoothing toward the previous
// frame so the mouth does not chatter. The gained value is clamped to 1 BEFORE
// smoothing (matching the renderer), so the smoothed output also stays within [0, 1].

/**
 * Compute the next mouth amplitude from an RMS reading.
 *
 * @param {number} rms - RMS amplitude of the current audio frame (typically 0..~0.2).
 * @param {object} params
 * @param {number} params.gain - multiplier applied to rms before clamping.
 * @param {number} params.prevAmp - previous frame's mouth amplitude.
 * @param {number} params.smoothing - EMA weight for prevAmp in [0, 1]; higher = smoother.
 * @returns {number} the next mouth amplitude, clamped to [0, 1].
 */
export function rmsToMouthAmp(rms, { gain, prevAmp, smoothing }) {
  const target = Math.min(1.0, rms * gain);
  return prevAmp * smoothing + target * (1 - smoothing);
}
