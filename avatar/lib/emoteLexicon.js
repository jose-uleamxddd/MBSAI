// emoteLexicon.js — Pure Spanish lexicon + transcript-matching for emotes.
//
// Extracted from emotes.js. Pure JS: knows nothing about three.js. In
// speech-to-speech the model must NEVER emit bracket/brace stage-direction tags
// (it would say them aloud), so emotes are derived ONLY from the live transcript
// text here. The renderer's EmoteEngine consumes classifyEmote + pickAnimationForCategory.

// ---------------------------------------------------------------------------
// Spanish lexicon — each category maps to the trigger fragments (lowercased,
// accent-insensitive after normalization) that activate it. Order of CATEGORY
// _PRIORITY decides which wins when several match in the same text.
// ---------------------------------------------------------------------------
export const LEXICON = Object.freeze({
  celebrate: [
    "increible",
    "felicidades",
    "genial",
    "wow",
    "guau",
    "super",
    "aplausos",
    "vamos",
    "buenisimo",
    "que emocion",
    "brutal",
    "espectacular",
    // Muletillas de anfitriona con energía — ella las suelta seguido.
    "que bueno",
    "me encanta",
    "eso",
    "energia",
    "celebrar",
    "fiesta",
    "listo",
    "arranca",
    "empezamos",
  ],
  surprised: [
    "no puede ser",
    "en serio",
    "no me digas",
    "o sea que",
    "que fuerte",
    "wow",
    "no puedo creer",
  ],
  warm: [
    "gracias",
    "carino",
    "lindo",
    "linda",
    "lindos",
    "con carino",
    "se les quiere",
    "de corazon",
    "abrazo",
    // Cercanía con el cliente.
    "bienvenidos",
    "familia",
    "comunidad",
    "juntos",
    "con ustedes",
    "de verdad",
  ],
  listen: [
    "pregunta",
    "cuentame",
    "a ver",
    "dejame pensar",
    "mmm",
    "buena pregunta",
    "interesante",
    "dime",
    "escucho",
  ],
  disagree: [
    "lamentablemente",
    "eso no",
    "no exactamente",
    "se sale de mi",
    "no es mi especialidad",
  ],
  angry: ["indignante", "no se vale", "me molesta"],
});

// Highest priority first. Strong reactions (celebrate/surprised) outrank the
// softer/ambient ones (warm/listen) so the gesture matches the punch line.
// NOTE: greet is intentionally ABSENT — the entrance gesture is exclusive to key H
// (playGreeting) and must NEVER be re-fired from the transcript ("hola/bienvenidos"
// used to re-trigger it and stomp the entrance mid-animation).
export const CATEGORY_PRIORITY = Object.freeze([
  "celebrate",
  "surprised",
  "disagree",
  "angry",
  "warm",
  "listen",
]);

// The grand-entrance clip. Exclusive to key H via playGreeting — NOT a transcript
// category (LEXICON/CATEGORY_PRIORITY omit greet on purpose). hello.vrma was removed:
// it faced away and stomped the entrance when the transcript used to re-fire greet.
export const GREETING_CLIP = "silly_tavern/greeting.vrma";

// Category -> candidate animation paths (relative to the animations base dir).
export const CATEGORY_ANIMATIONS = Object.freeze({
  greet: [GREETING_CLIP],
  celebrate: [
    "celebrate/jump.vrma",
    "celebrate/spin.vrma",
    "celebrate/peace_sign.vrma",
  ],
  warm: ["blush/blush.vrma"],
  listen: ["listen/look_around.vrma"],
  surprised: ["surprised/surprised.vrma"],
  disagree: ["shake_head/sad.vrma"],
  angry: ["angry/angry.vrma"],
  pose: ["pose/motion_pose.vrma", "idle/model_pose.vrma"],
});

// Cooldown between auto-played emotes so Norivy is not twitching every word.
// 1200 ms: gestos reactivos sin verse tiesa;
// el guard isBusy del EmoteEngine ya evita que un gesto nuevo pise uno en curso.
export const EMOTE_COOLDOWN_MS = 1200;
// Fallback hold (ms) after the H entrance during which transcript emotes are
// SUPPRESSED, so the greeting gesture is never stomped by celebrate/warm mid-play.
// The renderer overrides this with the greeting clip's REAL duration once preloaded;
// this default only applies if that preload failed.
export const GREETING_HOLD_MS = 4000;
// Only the LAST sentence of the accumulated transcript is classified — keeps
// the gesture in sync with what she is saying RIGHT NOW, not the whole answer.
const TAIL_SENTENCE_LOOKBEHIND = 140;

// Normalize for matching: lowercase + strip accents/diacritics. The lexicon is
// stored already-normalized so a single transform on the input is enough.
function normalize(text) {
  return text.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

// Take the trailing fragment of the transcript (last sentence-ish) so emotes
// react to the current phrase rather than re-firing on old text.
function tailFragment(text) {
  const normalized = normalize(text);
  if (normalized.length <= TAIL_SENTENCE_LOOKBEHIND) return normalized;
  const tail = normalized.slice(-TAIL_SENTENCE_LOOKBEHIND);
  const lastBreak = Math.max(
    tail.lastIndexOf(". "),
    tail.lastIndexOf("! "),
    tail.lastIndexOf("? "),
    tail.lastIndexOf("¡"),
    tail.lastIndexOf("¿"),
  );
  return lastBreak > 0 ? tail.slice(lastBreak + 1) : tail;
}

// Per-category rotation cursor — module-level so pickAnimationForCategory is a
// simple stateless-looking helper while still rotating across calls.
const rotationCounters = new Map();

/**
 * Classify the latest transcript text into an emote category.
 *
 * @param {string} text - accumulated transcript (the tail is what matters).
 * @returns {string|null} category from CATEGORY_PRIORITY, or null if nothing strong.
 */
export function classifyEmote(text) {
  if (!text || typeof text !== "string") return null;
  const fragment = tailFragment(text);
  if (!fragment.trim()) return null;
  for (const category of CATEGORY_PRIORITY) {
    const triggers = LEXICON[category];
    if (triggers.some((trigger) => fragment.includes(trigger))) {
      return category;
    }
  }
  return null;
}

/**
 * Pick one animation path for a category, rotating to avoid repetition.
 *
 * @param {string} category - emote category.
 * @returns {string|null} a relative animation path, or null if unknown category.
 */
export function pickAnimationForCategory(category) {
  const clips = CATEGORY_ANIMATIONS[category];
  if (!clips || clips.length === 0) return null;
  const index = rotationCounters.get(category) ?? 0;
  rotationCounters.set(category, (index + 1) % clips.length);
  return clips[index % clips.length];
}
