import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { EmoteEngine } from "../emotes.js";

// EmoteEngine is stateful (cooldown + last-category dedup + greeting hold) but
// headless-safe: it only uses Date.now() and the injected playClip callback — no
// DOM, no three.js. greet is NO LONGER a transcript category, so feeds here use
// celebrate ("increible") / warm ("gracias") triggers; the H entrance is playGreeting.
//
// NOTE: the engine starts with lastPlayedAt=0. The first feed plays only if
// Date.now() - 0 >= cooldownMs. In production Date.now() is huge so the first
// gesture always fires; under fake timers we start the clock past the cooldown
// (T0) to reproduce that real behavior instead of the t=0 edge case.
const T0 = 1_000_000;

describe("EmoteEngine", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(T0);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("throws if constructed without a playClip callback", () => {
    expect(() => new EmoteEngine()).toThrow(TypeError);
    expect(() => new EmoteEngine("not-a-fn")).toThrow(TypeError);
  });

  it("plays a matching gesture on first feed", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip);
    engine.feed("increible trabajo");
    expect(playClip).toHaveBeenCalledTimes(1);
    expect(playClip.mock.calls[0][0]).toMatch(/\.vrma$/);
  });

  it("does not play when the transcript matches nothing", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip);
    engine.feed("el clima esta templado");
    expect(playClip).not.toHaveBeenCalled();
  });

  it("suppresses a second gesture while still within the cooldown", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { cooldownMs: 1000 });
    engine.feed("gracias"); // plays (warm) at T0
    vi.setSystemTime(T0 + 500); // < cooldown
    engine.feed("increible"); // celebrate, but cooldown not elapsed
    expect(playClip).toHaveBeenCalledTimes(1);
  });

  it("plays a different category again once the cooldown has elapsed", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { cooldownMs: 1000 });
    engine.feed("gracias"); // warm at T0
    vi.setSystemTime(T0 + 1000); // == cooldown boundary (>= passes)
    engine.feed("increible trabajo"); // celebrate (different category)
    expect(playClip).toHaveBeenCalledTimes(2);
  });

  it("lets the same category 'breathe' (needs 2x cooldown to repeat)", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { cooldownMs: 1000 });
    engine.feed("increible"); // celebrate at T0
    vi.setSystemTime(T0 + 1500); // > cooldown but < 2x cooldown, same category
    engine.feed("increible de nuevo"); // celebrate again → suppressed
    expect(playClip).toHaveBeenCalledTimes(1);
    vi.setSystemTime(T0 + 2001); // > 2x cooldown from the last play
    engine.feed("increible otra vez"); // celebrate → now allowed
    expect(playClip).toHaveBeenCalledTimes(2);
  });

  it("playGreeting forces the greet gesture ignoring cooldown", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { cooldownMs: 100000 });
    engine.feed("increible"); // celebrate at T0
    engine.playGreeting(); // forced, same tick, ignores cooldown
    expect(playClip).toHaveBeenCalledTimes(2);
  });

  it("reset clears cooldown + last-category so the next feed plays immediately", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { cooldownMs: 100000 });
    engine.feed("gracias"); // warm at T0
    engine.reset(); // lastPlayedAt back to 0
    engine.feed("increible"); // celebrate: now - 0 = T0 >= cooldown → plays
    expect(playClip).toHaveBeenCalledTimes(2);
  });

  it("playGreeting sets a hold that suppresses transcript emotes until it elapses", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { greetingHoldMs: 4000 });
    engine.playGreeting(); // greet at T0, hold until T0+4000
    vi.setSystemTime(T0 + 1000); // within the hold
    engine.feed("increible"); // celebrate blocked by the greeting hold
    expect(playClip).toHaveBeenCalledTimes(1); // only the greeting
    vi.setSystemTime(T0 + 4000); // hold elapsed
    engine.feed("increible de verdad"); // now allowed
    expect(playClip).toHaveBeenCalledTimes(2);
  });

  it("reset PRESERVES the greeting hold (speech_started must not unprotect it)", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { greetingHoldMs: 4000 });
    engine.playGreeting(); // hold until T0+4000
    engine.reset(); // simulates onSpeechStarted resetting cooldown
    vi.setSystemTime(T0 + 1000); // within the hold
    engine.feed("increible"); // still blocked → hold survived reset
    expect(playClip).toHaveBeenCalledTimes(1); // greeting only
  });

  // --- isBusy: un gesto nuevo NO pisa uno que ya está corriendo ---------------
  // Cuando hay un emote activo, feed() no dispara y NO consume el cooldown, así el
  // gesto en curso llega completo y el próximo turno igual reacciona.
  it("does not play a new gesture while one is already running (isBusy true)", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { isBusy: () => true });
    engine.feed("increible"); // hay un gesto activo → NO dispara
    expect(playClip).not.toHaveBeenCalled();
  });

  it("does NOT consume the cooldown while busy: a later free feed still fires", () => {
    const playClip = vi.fn();
    let busy = true; // flag mutable: gesto activo ahora, libre después
    const engine = new EmoteEngine(playClip, {
      cooldownMs: 1000,
      isBusy: () => busy,
    });
    engine.feed("increible"); // busy → bloqueado, lastPlayedAt intacto
    expect(playClip).not.toHaveBeenCalled();
    busy = false; // el gesto activo terminó (returnToIdle puso emoteAction=null)
    engine.feed("increible de verdad"); // libre y cooldown NO se gastó → dispara
    expect(playClip).toHaveBeenCalledTimes(1);
  });

  it("plays normally when isBusy defaults to false", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip); // sin isBusy → nunca ocupado
    engine.feed("increible trabajo");
    expect(playClip).toHaveBeenCalledTimes(1);
  });

  it("playGreeting fires even while busy (the H entrance ignores isBusy)", () => {
    const playClip = vi.fn();
    const engine = new EmoteEngine(playClip, { isBusy: () => true });
    engine.playGreeting(); // el saludo siempre entra, aunque haya gesto activo
    expect(playClip).toHaveBeenCalledTimes(1);
  });
});
