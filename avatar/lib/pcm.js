// pcm.js — Pure PCM16 base64 decoding for the audio pipeline.
//
// Extracted from renderer.js. Pure: no three.js, no window/document. Uses `atob`
// which jsdom provides, so it runs headless. A corrupt base64 frame is dropped
// with evidence (returns an empty Int16Array) instead of crashing the audio
// pipeline mid-event; an odd byte length discards the trailing half-sample.

/**
 * Decode a base64 PCM16 (little-endian) payload into an Int16Array.
 *
 * @param {string} base64 - base64-encoded PCM16 mono chunk (may be empty/nullish).
 * @returns {Int16Array} decoded samples; empty on corrupt base64. An odd byte
 *   count logs a warning and drops the last byte (trailing half-sample).
 */
export function base64ToInt16(base64) {
  try {
    const binary = atob(base64 || "");
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    if (bytes.length % 2 !== 0) {
      // Chunk PCM16 cortado a mitad de muestra: descartamos 1 byte pero lo
      // logueamos para que sea diagnosticable si empieza a pasar en vivo.
      console.warn("[renderer] audio PCM16 de longitud impar — 1 byte descartado");
    }
    // PCM16 little-endian; reinterpret the byte buffer as Int16.
    return new Int16Array(bytes.buffer, 0, Math.floor(bytes.length / 2));
  } catch (err) {
    // Frame de audio corrupto (base64 inválido): descartar con evidencia en vez
    // de romper el pipeline de audio en silencio a mitad de un evento en vivo.
    console.error("[renderer] audio frame corrupto, descartado:", err);
    return new Int16Array(0);
  }
}
