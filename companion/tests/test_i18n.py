"""Unit tests for companion.i18n multilingual support."""
import unittest

from companion.i18n import (
    DEFAULT,
    ESPEAK_VOICES,
    LANGUAGES,
    PHRASES,
    SAY_VOICES,
    get,
    get_hazard_key,
)


class TestI18n(unittest.TestCase):
    def test_supported_languages(self):
        self.assertIn("en", LANGUAGES)
        self.assertIn("ko", LANGUAGES)
        self.assertIn("zh", LANGUAGES)
        self.assertIn("ja", LANGUAGES)
        self.assertIn("es", LANGUAGES)
        self.assertEqual(DEFAULT, "en")

    def test_voice_mappings(self):
        for lang in LANGUAGES:
            self.assertIn(lang, ESPEAK_VOICES)
            self.assertIn(lang, SAY_VOICES)
            self.assertTrue(len(ESPEAK_VOICES[lang]) > 0)
            if lang != "en":
                self.assertIsNotNone(SAY_VOICES[lang])

    def test_all_phrases_have_translations(self):
        """Every phrase in the phrase bank must have a translation in every supported language."""
        for phrase_id, translations in PHRASES.items():
            for lang in LANGUAGES:
                self.assertIn(lang, translations, f"Missing translation for '{phrase_id}' in language '{lang}'")
                self.assertTrue(len(translations[lang]) > 0, f"Empty translation for '{phrase_id}' in language '{lang}'")

    def test_get_fallback(self):
        """Requesting an unknown language falls back to English."""
        en_val = get("obstacle_ahead", "en")
        self.assertEqual(get("obstacle_ahead", "xx"), en_val)
        self.assertEqual(get("obstacle_ahead", None), en_val)

    def test_get_formatting(self):
        formatted = get("battery_level", "en").format(percent=85)
        self.assertEqual(formatted, "Battery 85 percent.")
        formatted_ko = get("battery_level", "ko").format(percent=85)
        self.assertEqual(formatted_ko, "배터리 85 퍼센트.")

    def test_hazard_keys(self):
        key = get_hazard_key("urgent", "head", "left")
        self.assertEqual(key, "stop_head_obstacle_ahead_left")
        self.assertIn(key, PHRASES)
        self.assertEqual(get(key, "ko"), "멈추세요. 전방 왼쪽 머리 높이에 장애물.")

        key_ahead = get_hazard_key("warning", "chest", "center")
        self.assertEqual(key_ahead, "obstacle_ahead")
        self.assertIn(key_ahead, PHRASES)


if __name__ == "__main__":
    unittest.main()

