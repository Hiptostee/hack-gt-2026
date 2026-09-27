# Other Languages — Specification

Multilingual support for the wearable spatial guide. The user speaks in their
language; the device answers in the same language. Local offline phrases
(hazard warnings, help/status, errors) follow a configured device language.

Status: spec draft, 2026-09-26 22:10. No code yet. Depends on the existing
voice companion and Guardian code on `feature/guardian`.

---

## 1. What changes and why

The device currently speaks only English. HackGT draws an international
audience, and answering in the user's own language is a direct usability gain —
not a bolted-on feature. A Korean-speaking blind user gains nothing from an
English-only scene description.

**Demo languages:** English, Korean (한국어), Chinese (中文), Japanese (日本語),
Spanish (Español).

| Layer | Current | After |
| --- | --- | --- |
| Scene questions (Gemini) | English answers only (system prompt doesn't mention language) | Gemini answers in the language the user spoke |
| Cloud TTS (ElevenLabs companion) | `eleven_flash_v2_5` — already multilingual | No model change needed; voice quality per language TO BE VALIDATED |
| Cloud TTS (Guardian agent) | `eleven_flash_v2` — **English only** | Switch to `eleven_flash_v2_5`; add language settings on the agent |
| Local offline phrases | Hardcoded English strings | Pre-rendered phrase bank per configured device language |
| `device_action` / schema fields | English | **Stay English** — these are code constants, not user-facing |
| SMS text | English | Stay in the **contact's** language (English initially); the contact reads it |

### Scope boundary

- **In scope:** answering in the user's language, Guardian in the user's
  language, pre-rendered local phrases for the five demo languages.
- **Out of scope:** runtime language switching mid-session, auto-detecting
  language for local phrases (local phrases follow a config, not speech
  detection), translating SMS text to the contact's language, adding languages
  beyond the five listed above.

---

## 2. Architecture

No new processes or services. Changes are configuration and string tables.

```
User speaks (any language)
  → Gemini (already multilingual)
  → answer in user's language
  → ElevenLabs eleven_flash_v2_5 (already multilingual)
  → spoken answer in user's language

Local phrases (hazard, help, errors)
  → pre-rendered at startup from PHRASES[device_lang]
  → stored as PCM clips, same as today's English hazard phrase
```

### 2.1 Configuration

One new environment variable:

| Variable | Default | Values |
| --- | --- | --- |
| `DEVICE_LANG` | `en` | `en`, `ko`, `zh`, `ja`, `es` |

This controls:
- Which local phrase set is rendered at startup
- The language hint added to the Gemini system prompt
- The Guardian agent's language setting (if the agent supports dynamic
  language; otherwise the agent prompt says "answer in the language the user
  spoke")

It does **not** control Gemini's answer language — Gemini answers in whatever
language the user spoke, regardless of `DEVICE_LANG`. The device language is
for offline phrases that have no speech input to match.

---

## 3. Changes by component

### 3.1 Gemini system prompt (`companion/voice/gemini.py`)

Add one sentence to `SYSTEM`:

```
Answer in the same language the user spoke. Keep device_action values,
landmark, and all schema field names in English regardless of the user's
language. If you cannot determine the language, answer in English.
```

No schema changes. `device_action` enum values stay English. `landmark` stays
in the language of the sign/label observed (if a sign says "출구" the landmark
is "출구", not "exit").

### 3.2 ElevenLabs companion TTS (`companion/voice/speech.py`)

Already uses `eleven_flash_v2_5`, which is multilingual. **No change needed.**
The model auto-detects the language of the text it receives.

TO BE VALIDATED: voice quality and pronunciation for Korean, Chinese, Japanese,
and Spanish with the current voice ID. Some voices sound better in certain
languages; if the default voice is poor in a demo language, pick a different
`ELEVENLABS_VOICE_ID` that sounds acceptable across all five.

### 3.3 Guardian agent (ElevenLabs dashboard + `companion/guardian/`)

Two changes:

1. **TTS model:** switch from `eleven_flash_v2` (English only) to
   `eleven_flash_v2_5` (multilingual) in the ElevenLabs agent dashboard.

2. **Agent system prompt:** add to the Guardian prompt:

   ```
   Answer in the same language the user spoke. If you cannot determine their
   language, use English. Your tool calls, parameter names, and status updates
   stay in English regardless.
   ```

3. **First message:** the greeting is in English by default. If the agent
   supports a `language` dynamic variable, send `DEVICE_LANG` and let the
   greeting match. Otherwise, the greeting stays English and the agent
   switches after the user's first turn — acceptable for the demo.

4. **Local Guardian announcements** (`EXIT_LINES`, cancel window text, SMS
   preview frame): these are spoken by local TTS, not the agent. They follow
   `DEVICE_LANG` via the phrase table (§3.4).

Record the model change in `companion/guardian/agent.md`.

### 3.4 Local phrase table (`companion/i18n/phrases.py` — new file)

A dictionary of all strings spoken by local TTS, keyed by language code.
Pre-rendered to PCM at startup alongside the hazard phrase bank.

Strings to translate (exhaustive list from the current codebase):

**Hazard phrases** (from `companion/voice/hazards.py`):
- "Obstacle ahead."
- "Head-height obstacle ahead"
- "Head-height obstacle ahead, left"
- "Head-height obstacle ahead, right"
- "Obstacle ahead, left"
- "Obstacle ahead, right"
- "Stop. Obstacle ahead." (and all left/right/head variants with "Stop.")
- "Hazard sensing unavailable. Use your cane."
- "Hazard warnings ready. Floor hazards are not monitored."
- "Hazard sensing restored."

**Companion phrases** (from `companion/voice/__main__.py`):
- "Ready."
- "I did not hear anything."
- "There is nothing to repeat yet."
- "Backpack guidance is unavailable here."
- "Guidance stopped."
- "Guardian mode isn't set up on this device."
- "Something went wrong."

**Status phrases** (from `companion/voice/__main__.py` `status_text()`):
- "Network reachable." / "No network."
- "Camera: {status}."
- "Gemini key configured." / "No Gemini key."
- "Battery {n} percent." / "Battery level unknown."

**Guardian exit phrases** (from `companion/guardian/session.py`):
- "Cancelled."
- "Guardian ended."
- "I lost the connection to guardian mode."
- "Guardian isn't available right now."
- "Guardian needs the network."

**Guardian cancel window** (from `companion/guardian/session.py`):
- "Opening guardian mode. Tap to cancel."

**SMS preview frame** (from `companion/guardian/sms.py`):
- "I'll text {contact}: {text}. Hold the button and say yes to send, or no to cancel."

### 3.5 Local TTS engine language

`espeak-ng` and macOS `say` both support multiple languages:
- `espeak-ng -v ko` / `espeak-ng -v zh` / `espeak-ng -v ja` / `espeak-ng -v es`
- macOS `say -v Yuna` (Korean), `say -v Ting-Ting` (Chinese), `say -v Kyoko` (Japanese), `say -v Paulina` (Spanish)

The `render_local()` method in `speech.py` needs a `lang` parameter to select
the right voice. For pre-rendered hazard/status phrases this is `DEVICE_LANG`;
for Gemini answers that fall back to local TTS, the language is the answer
text's language (which we can approximate as `DEVICE_LANG` since most users
will set it to match their spoken language).

---

## 4. Implementation plan

| Step | Work | Evidence |
| --- | --- | --- |
| 1 | Create `companion/i18n/phrases.py` with the phrase table for all 5 languages | Translations reviewed; module imports cleanly |
| 2 | Add `DEVICE_LANG` env var and wire it through `main()` | Startup log shows the configured language |
| 3 | Update `gemini.py` SYSTEM prompt to add the "answer in the user's language" instruction | Gemini answers in Korean when asked in Korean |
| 4 | Update `speech.py` `render_local()` to accept a language and select the right TTS voice | Local phrases render in Korean/Chinese/Japanese/Spanish |
| 5 | Update `hazards.py` to render phrases from the phrase table using `DEVICE_LANG` | Hazard phrase plays in the configured language |
| 6 | Update `__main__.py` to use translated strings from the phrase table | Status/help/error messages in the configured language |
| 7 | Update Guardian `session.py` EXIT_LINES and cancel-window text from the phrase table | Guardian local announcements in the configured language |
| 8 | Switch Guardian agent TTS model to `eleven_flash_v2_5` in the dashboard; update `agent.md` | Guardian agent speaks the user's language |
| 9 | Add "answer in the user's language" to the Guardian agent system prompt | Guardian answers in Korean when user speaks Korean |
| 10 | Validate voice quality per language | Each demo language sounds acceptable |

Steps 1–7 are code changes. Steps 8–9 are dashboard config. Step 10 is manual
listening.

---

## 5. Failure behavior

| Condition | Behavior |
| --- | --- |
| `DEVICE_LANG` unset or unrecognized | Default to `en`; log a warning |
| `espeak-ng` doesn't have the requested language voice | Fall back to English local TTS; log which language failed |
| Gemini can't determine the user's language | Answer in English (existing behavior) |
| ElevenLabs voice sounds bad in a language | Use a different voice ID via `ELEVENLABS_VOICE_ID`; document which voices work for which languages |

---

## 6. Relationship to other modules

- **Hazard warnings:** pre-rendered clips change language at startup, not at
  runtime. No latency impact — same PCM playback path.
- **Voice companion:** Gemini prompt change is additive. No pipeline change.
- **Guardian:** TTS model swap in the dashboard. Local announcements from the
  phrase table. Agent prompt change is additive.
- **Navigation/tactile:** unaffected. Direction cues are non-verbal.
- **SMS:** text stays in the contact's language (English). The spoken preview
  frame follows `DEVICE_LANG`.

---

## 7. Open decisions

| Item | Status |
| --- | --- |
| Which ElevenLabs voice sounds best across all 5 languages | TO BE VALIDATED by ear |
| Guardian agent `language` dynamic variable | TO BE CHECKED in ElevenLabs dashboard; may not exist |
| Whether to include Simplified or Traditional Chinese | Defaulting to Simplified (`zh`); TO BE DECIDED |
| Runtime language switching (say "switch to Korean") | OUT OF SCOPE for initial implementation |
| `status_text()` template strings with dynamic values | Need `format()`-compatible translations; see §3.4 |

---

## 8. Acceptance criteria

1. With `DEVICE_LANG=ko`, asking a question in Korean produces a Korean spoken
   answer through ElevenLabs.
2. With `DEVICE_LANG=ko`, the hazard phrase plays in Korean.
3. With `DEVICE_LANG=ko`, double-tap help/status speaks in Korean.
4. With `DEVICE_LANG=ko`, Guardian greets and converses in Korean when the
   user speaks Korean.
5. With `DEVICE_LANG=ko`, Guardian exit announcements are in Korean.
6. `device_action` values in Gemini responses remain English regardless of the
   user's language.
7. SMS text body remains in English.
8. With `DEVICE_LANG` unset, everything works in English (no regression).
9. With no network, local TTS plays hazard and status phrases in the configured
   language.
10. Repeat for `zh`, `ja`, `es` (same criteria).
