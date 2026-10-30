"""Unit tests for config/keyring_manager.py."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch
import keyring
from keyring.backend import KeyringBackend

from config.keyring_manager import (
    KEY_NAME,
    OLLAMA_KEY_NAME,
    SERVICE_NAME,
    _key_name_for_provider,
    check_ollama_status,
    delete_api_key,
    fetch_available_models,
    get_api_key,
    save_api_key,
    test_api_key,
    test_groq_api_key,
)


class _MemoryKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self._passwords = {}

    def get_password(self, service, username):
        return self._passwords.get((service, username))

    def set_password(self, service, username, password):
        self._passwords[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) in self._passwords:
            del self._passwords[(service, username)]
        else:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError("Password not found")


class TestKeyringManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            keyring.get_password("test-probe", "probe-user")
        except Exception:
            keyring.set_keyring(_MemoryKeyring())
    def test_save_and_retrieve_api_key(self):
        test_key = "test-secret-key-12345"
        save_api_key(test_key)
        retrieved = get_api_key()
        self.assertEqual(retrieved, test_key)

        # Clean up
        deleted = delete_api_key()
        self.assertTrue(deleted)
        self.assertIsNone(get_api_key())

    def test_save_empty_key_raises(self):
        with self.assertRaises(ValueError):
            save_api_key("")

        with self.assertRaises(ValueError):
            save_api_key("   ")

    def test_delete_absent_key_returns_true(self):
        # Ensure it's deleted
        delete_api_key()
        # Deleting again when not present should return True gracefully
        self.assertTrue(delete_api_key())

    def test_test_api_key_empty(self):
        valid, msg = test_api_key("")
        self.assertFalse(valid)
        self.assertIn("empty", msg.lower())

    def test_test_api_key_invalid(self):
        # Test with dummy key against Google GenAI client
        valid, msg = test_api_key("fake_invalid_gemini_key")
        self.assertFalse(valid)
        self.assertTrue(len(msg) > 0)

    def test_test_api_key_success_mock(self):
        # Test mocked successful call
        with patch("google.genai.Client") as mock_client_cls:
            mock_instance = MagicMock()
            mock_client_cls.return_value = mock_instance
            mock_model = MagicMock()
            mock_model.name = "models/gemini-3.8-flash"
            mock_instance.models.list.return_value = [mock_model]

            valid, msg = test_api_key("valid_dummy_key")
            self.assertTrue(valid)
            self.assertIn("verified", msg.lower())
            mock_instance.models.list.assert_called_once()

    def test_save_and_retrieve_groq_key(self):
        groq_key = "gsk_test_secret_key_12345"
        save_api_key(groq_key, provider="groq")
        retrieved = get_api_key("groq")
        self.assertEqual(retrieved, groq_key)

        deleted = delete_api_key("groq")
        self.assertTrue(deleted)
        self.assertIsNone(get_api_key("groq"))

    def test_test_groq_api_key_empty(self):
        valid, msg = test_groq_api_key("")
        self.assertFalse(valid)
        self.assertIn("empty", msg.lower())

    def test_test_groq_api_key_mock_success(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_instance = MagicMock()
            mock_openai_cls.return_value = mock_instance
            mock_instance.models.list.return_value = MagicMock(data=[MagicMock(id="llama-3.3-70b-versatile")])

            valid, msg = test_groq_api_key("test_groq_key", model="llama-3.3-70b-versatile")
            self.assertTrue(valid)
            self.assertIn("verified", msg.lower())
            mock_instance.models.list.assert_called_once()

    def test_fetch_available_models_wrapper(self):
        with patch("agent.llm_client.fetch_available_models") as mock_fetch:
            mock_fetch.return_value = ["test-model-1", "test-model-2"]
            models = fetch_available_models("groq", "test-key")
            self.assertEqual(models, ["test-model-1", "test-model-2"])
            mock_fetch.assert_called_once_with("groq", "test-key")

    def test_check_ollama_status_running(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_instance = MagicMock()
            mock_openai_cls.return_value = mock_instance
            mock_instance.models.list.return_value = MagicMock(
                data=[MagicMock(id="qwen2.5-coder:7b")]
            )

            success, message = check_ollama_status("http://127.0.0.1:11434/v1")
            self.assertTrue(success)
            self.assertEqual(message, "Ollama is running locally")
            mock_openai_cls.assert_called_once_with(
                base_url="http://127.0.0.1:11434/v1", api_key="ollama", timeout=3.0
            )
            mock_instance.models.list.assert_called_once()

    def test_check_ollama_status_not_running(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_instance = MagicMock()
            mock_openai_cls.return_value = mock_instance
            mock_instance.models.list.side_effect = ConnectionError("Connection refused")

            success, message = check_ollama_status("http://127.0.0.1:11434/v1")
            self.assertFalse(success)
            self.assertEqual(
                message, "Could not connect to Ollama daemon on localhost:11434"
            )

    def test_ollama_key_name_for_provider(self):
        self.assertEqual(_key_name_for_provider("ollama"), OLLAMA_KEY_NAME)


if __name__ == "__main__":
    unittest.main()
