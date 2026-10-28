// Revelado "paced" de los subtítulos de Norivy. El transcript de OpenAI llega
// casi de golpe (velocidad de GENERACIÓN del modelo), pero el audio se reproduce
// a velocidad de habla — sin esto el texto se adelanta varias oraciones a la voz.
// `advanceReveal` avanza un cursor de caracteres a `rate` chars/seg, sin pasarse
// del texto ya recibido ni retroceder.

/**
 * @param {number} shownChars  Cursor actual (float) de caracteres ya revelados.
 * @param {number} fullLength  Largo del texto recibido hasta ahora.
 * @param {number} dtSeconds   Delta de tiempo del frame (segundos).
 * @param {number} rate        Caracteres por segundo a revelar (~velocidad de habla).
 * @returns {number} Nuevo cursor: avanza `rate*dt`, sin retroceder ni pasar `fullLength`.
 */
export function advanceReveal(shownChars, fullLength, dtSeconds, rate) {
  const dt = dtSeconds > 0 ? dtSeconds : 0;
  const next = shownChars + rate * dt;
  return Math.min(fullLength, Math.max(shownChars, next));
}
