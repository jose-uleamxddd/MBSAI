// emotes.js — EmoteEngine: drives gesture playback from streaming transcript text.
//
// The pure Spanish lexicon + transcript matching live in ./lib/emoteLexicon.js
// (no three.js knowledge). This module owns only the stateful engine: the
// renderer constructs an EmoteEngine with a `playClip(relPath)` callback and
// feeds it transcript text as it streams; the engine decides WHICH clip and WHEN
// (respecting a cooldown).
import {
  classifyEmote,
  pickAnimationForCategory,
  EMOTE_COOLDOWN_MS,
  GREETING_HOLD_MS,
} from "./lib/emoteLexicon.js";

/**
 * EmoteEngine — drives gesture playback from streaming transcript text.
 *
 * The renderer owns three.js; it passes a `playClip(relPath)` callback. The
 * engine only decides WHICH clip and WHEN (respecting a cooldown).
 */
export class EmoteEngine {
  /**
   * @param {(relPath: string) => void} playClip - plays a relative .vrma path.
   * @param {{ cooldownMs?: number, greetingHoldMs?: number, isBusy?: () => boolean }} [options]
   */
  constructor(playClip, options = {}) {
    if (typeof playClip !== "function") {
      throw new TypeError("EmoteEngine requires a playClip(relPath) callback");
    }
    this.playClip = playClip;
    this.cooldownMs = options.cooldownMs ?? EMOTE_COOLDOWN_MS;
    this.greetingHoldMs = options.greetingHoldMs ?? GREETING_HOLD_MS;
    // ¿Hay un gesto corriendo ahora mismo? El renderer lo cablea con el emote
    // activo del mixer; por defecto nunca está ocupado (para tests puros).
    this.isBusy = options.isBusy ?? (() => false);
    this.lastPlayedAt = 0;
    this.lastCategory = null;
    // While Date.now() < suppressUntil, feed() plays nothing — protects the H
    // entrance gesture from being stomped by transcript emotes mid-play.
    this.suppressUntil = 0;
  }

  /**
   * Feed accumulated transcript text. Classifies and, if the cooldown has
   * elapsed, plays the matching gesture. Repeated identical categories are
   * skipped while still within cooldown to avoid stutter.
   *
   * @param {string} transcriptText
   */
  feed(transcriptText) {
    // Si ya hay un gesto corriendo, no lo pisamos: salimos ANTES de clasificar y
    // sin tocar el cooldown, así el gesto activo llega completo y el próximo turno
    // igual reacciona (no se "gastó" la ventana mientras ella gesticulaba).
    if (this.isBusy()) return;
    const category = classifyEmote(transcriptText);
    if (!category) return;
    const now = Date.now();
    if (now < this.suppressUntil) return; // greeting hold — don't stomp the entrance
    const sinceLast = now - this.lastPlayedAt;
    if (sinceLast < this.cooldownMs) return;
    if (category === this.lastCategory && sinceLast < this.cooldownMs * 2) {
      // Same emotion twice in a row too soon — let it breathe.
      return;
    }
    const relPath = pickAnimationForCategory(category);
    if (!relPath) return;
    this.lastPlayedAt = now;
    this.lastCategory = category;
    this.playClip(relPath);
  }

  /** Force the greeting gesture (key H grand entrance) ignoring cooldown. */
  playGreeting() {
    const relPath = pickAnimationForCategory("greet");
    if (!relPath) return;
    const now = Date.now();
    this.lastPlayedAt = now;
    this.lastCategory = "greet";
    // Hold off transcript emotes for the gesture's duration so nothing stomps it.
    this.suppressUntil = now + this.greetingHoldMs;
    this.playClip(relPath);
  }

  /** Reset cooldown + last-category state (call on a new response). */
  reset() {
    this.lastPlayedAt = 0;
    this.lastCategory = null;
    // NOTE: suppressUntil is intentionally NOT cleared — onSpeechStarted calls this
    // right after the H entrance, and dropping the hold would let the first
    // transcript emote stomp the greeting gesture.
  }
}
