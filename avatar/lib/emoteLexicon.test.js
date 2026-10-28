import { describe, it, expect } from "vitest";
import {
  classifyEmote,
  pickAnimationForCategory,
  LEXICON,
  CATEGORY_PRIORITY,
  CATEGORY_ANIMATIONS,
  EMOTE_COOLDOWN_MS,
  GREETING_CLIP,
  GREETING_HOLD_MS,
} from "./emoteLexicon.js";

describe("emoteLexicon constants", () => {
  it("exposes the frozen lexicon, priority order and animation map", () => {
    expect(Object.isFrozen(LEXICON)).toBe(true);
    expect(Object.isFrozen(CATEGORY_PRIORITY)).toBe(true);
    expect(Object.isFrozen(CATEGORY_ANIMATIONS)).toBe(true);
    expect(EMOTE_COOLDOWN_MS).toBe(1200);
    // celebrate leads now — greet is no longer a transcript category.
    expect(CATEGORY_PRIORITY[0]).toBe("celebrate");
  });

  it("keeps greet OUT of the transcript lexicon (H-only, never re-fired by text)", () => {
    // The grand-entrance gesture is exclusive to key H (playGreeting). If "greet"
    // lived in the transcript lexicon, Norivy saying "hola/bienvenidos" would
    // re-fire it and stomp the entrance mid-animation.
    expect(LEXICON.greet).toBeUndefined();
    expect(CATEGORY_PRIORITY).not.toContain("greet");
  });

  it("still exposes a single greet clip for playGreeting (no back-facing hello)", () => {
    // playGreeting picks from CATEGORY_ANIMATIONS.greet directly; it stays a single
    // deterministic clip (hello.vrma removed — it faced away and stomped the entrance).
    expect(CATEGORY_ANIMATIONS.greet).toEqual([GREETING_CLIP]);
    expect(GREETING_CLIP).toBe("silly_tavern/greeting.vrma");
  });

  it("defines a positive greeting hold to protect the entrance gesture", () => {
    expect(GREETING_HOLD_MS).toBeGreaterThan(0);
  });
});

describe("classifyEmote", () => {
  it("no longer classifies greet from the transcript (greet is H-only)", () => {
    // "hola" used to map to greet; greet is not a transcript category anymore.
    expect(classifyEmote("hola a todos")).toBeNull();
  });

  it("matches a celebrate trigger word", () => {
    expect(classifyEmote("esto es increible")).toBe("celebrate");
  });

  it("returns null when nothing strong matches", () => {
    expect(classifyEmote("el clima esta templado hoy")).toBeNull();
  });

  it("classifies new presenter phrases into their category (más gestos reactivos)", () => {
    // Palabras comunes de presentadora que Norivy usa seguido → ahora sí gesticula.
    expect(classifyEmote("bienvenidos a todos")).toBe("warm");
    expect(classifyEmote("vamos con energia")).toBe("celebrate");
    expect(classifyEmote("cuentame mas de eso")).toBe("celebrate"); // "eso" (celebrate) manda sobre "cuentame" (listen)
    expect(classifyEmote("a ver cuentame")).toBe("listen"); // sin trigger fuerte → listen
    expect(classifyEmote("no puedo creer lo que escucho")).toBe("surprised");
  });

  it("returns null for empty / non-string input", () => {
    expect(classifyEmote("")).toBeNull();
    expect(classifyEmote(null)).toBeNull();
    expect(classifyEmote(undefined)).toBeNull();
    expect(classifyEmote(123)).toBeNull();
  });

  it("is case-insensitive and accent-insensitive (normalized matching)", () => {
    // "increíble" (accented, mixed case) normalizes to the stored "increible".
    expect(classifyEmote("INCREÍBLE")).toBe("celebrate");
    expect(classifyEmote("Increíble trabajo")).toBe("celebrate");
  });

  it("honors priority order when several categories could match", () => {
    // "increible" (celebrate) + "gracias" (warm) in the same tail → celebrate wins.
    expect(classifyEmote("increible, gracias por venir")).toBe("celebrate");
  });

  it("classifies only the trailing sentence, not old text", () => {
    // A long lead ending in a fresh warm sentence → warm, not an earlier match.
    const long =
      "estuvimos hablando de muchos temas tecnicos durante un largo rato sin nada especial. " +
      "de verdad gracias a todos";
    expect(classifyEmote(long)).toBe("warm");
  });

  it("cuts at the last sentence break inside the >140-char lookbehind window", () => {
    // Lead a celebrate trigger far back, then a period, then a warm-only tail. The
    // whole string is >140 chars so tailFragment slices the last 140 and cuts at
    // ". " → only the warm tail is classified, the lead celebrate is dropped.
    const filler = "increible ".repeat(20); // ~200 chars, celebrate triggers
    const text = `${filler}. muchas gracias de corazon a todos ustedes`;
    expect(text.length).toBeGreaterThan(140);
    expect(classifyEmote(text)).toBe("warm");
  });

  it("returns the full lookbehind tail when no sentence break is present", () => {
    // >140 chars with NO ". "/"!"/"?"/"¡"/"¿" in the last 140 → lastBreak <= 0,
    // so the whole 140-char tail is used and the trailing celebrate trigger wins.
    const filler = "palabra ".repeat(30); // ~240 chars, no sentence breaks
    const text = `${filler} increible`;
    expect(text.length).toBeGreaterThan(140);
    expect(classifyEmote(text)).toBe("celebrate");
  });
});

describe("pickAnimationForCategory", () => {
  it("returns null for an unknown category", () => {
    expect(pickAnimationForCategory("nonexistent")).toBeNull();
  });

  it("returns the single deterministic greet clip (used by playGreeting)", () => {
    expect(pickAnimationForCategory("greet")).toBe("silly_tavern/greeting.vrma");
    expect(pickAnimationForCategory("greet")).toBe("silly_tavern/greeting.vrma");
  });

  it("returns the single deterministic clip for a one-clip category", () => {
    // warm has exactly one clip → always that path regardless of rotation cursor.
    expect(pickAnimationForCategory("warm")).toBe("blush/blush.vrma");
    expect(pickAnimationForCategory("warm")).toBe("blush/blush.vrma");
  });

  it("rotates across all clips of a multi-clip category then repeats", () => {
    // celebrate has 3 clips. Regardless of the current module-level cursor, three
    // consecutive picks must cover all three distinct clips (rotation, no repeats).
    const clips = CATEGORY_ANIMATIONS.celebrate;
    const picks = [
      pickAnimationForCategory("celebrate"),
      pickAnimationForCategory("celebrate"),
      pickAnimationForCategory("celebrate"),
    ];
    expect(new Set(picks)).toEqual(new Set(clips));
    // The 4th pick wraps back to whatever the 1st was (cycle length = clip count).
    expect(pickAnimationForCategory("celebrate")).toBe(picks[0]);
  });
});
