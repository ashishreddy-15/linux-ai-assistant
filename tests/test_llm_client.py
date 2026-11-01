"""Unit tests for agent/llm_client.py retry logic and error classification."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from google.genai import errors, types

from agent.llm_client import (
    OLLAMA_DEFAULT_MODEL,
    BaseLLMClient,
    GeminiAgentClient,
    GroqAgentClient,
    OpenAIAgentClient,
    create_llm_client,
    fetch_available_models,
    is_transient_error,
)
from config.provider_registry import PROVIDER_CONFIGS


class TestTransientErrorClassification(unittest.TestCase):
    def test_503_unavailable_server_error(self):
        err = errors.ServerError(
            503,
            {
                "error": {
                    "code": 503,
                    "message": "This model is currently experiencing high demand.",
                    "status": "UNAVAILABLE",
                }
            },
        )
        self.assertTrue(is_transient_error(err))

    def test_429_resource_exhausted_client_error(self):
        err = errors.ClientError(
            429,
            {
                "error": {
                    "code": 429,
                    "message": "Quota exceeded",
                    "status": "RESOURCE_EXHAUSTED",
                }
            },
        )
        self.assertTrue(is_transient_error(err))

    def test_string_fallback_high_demand(self):
        err = Exception("503 UNAVAILABLE: This model is currently experiencing high demand.")
        self.assertTrue(is_transient_error(err))

    def test_string_fallback_resource_exhausted(self):
        err = Exception("429 RESOURCE_EXHAUSTED: Rate limit reached.")
        self.assertTrue(is_transient_error(err))

    def test_non_transient_400_client_error(self):
        err = errors.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "API key not valid",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
        self.assertFalse(is_transient_error(err))

    def test_non_transient_404_not_found(self):
        err = errors.ClientError(
            404,
            {
                "error": {
                    "code": 404,
                    "message": "Model not found",
                    "status": "NOT_FOUND",
                }
            },
        )
        self.assertFalse(is_transient_error(err))

    def test_standard_exception(self):
        err = ValueError("Invalid parameter value")
        self.assertFalse(is_transient_error(err))


class TestGeminiAgentClientRetries(unittest.TestCase):
    def setUp(self):
        self.client = GeminiAgentClient(
            api_key="test-api-key",
            system_prompt="Test system prompt",
            max_retries=3,
            initial_retry_delay=0.001,  # Fast retries for testing
            backoff_factor=1.5,
        )
        self.client._client = MagicMock()
        self.mock_models = self.client._client.models

    def test_successful_call_on_first_attempt(self):
        mock_response = MagicMock()
        mock_response.candidates = [MagicMock(finish_reason="STOP")]
        self.mock_models.generate_content.return_value = mock_response

        history = [types.Content(role="user", parts=[types.Part(text="hello")])]
        resp = self.client.generate(history)

        self.assertEqual(resp, mock_response)
        self.assertEqual(self.mock_models.generate_content.call_count, 1)

    def test_transient_error_retries_and_succeeds(self):
        err503 = errors.ServerError(
            503,
            {
                "error": {
                    "code": 503,
                    "message": "High demand",
                    "status": "UNAVAILABLE",
                }
            },
        )
        mock_response = MagicMock()
        mock_response.candidates = [MagicMock(finish_reason="STOP")]
        # Fails twice, succeeds on 3rd attempt (2 retries)
        self.mock_models.generate_content.side_effect = [err503, err503, mock_response]

        history = [types.Content(role="user", parts=[types.Part(text="test")])]
        resp = self.client.generate(history)

        self.assertEqual(resp, mock_response)
        self.assertEqual(self.mock_models.generate_content.call_count, 3)

    def test_transient_error_exhausts_retries_and_raises(self):
        err503 = errors.ServerError(
            503,
            {
                "error": {
                    "code": 503,
                    "message": "High demand",
                    "status": "UNAVAILABLE",
                }
            },
        )
        # Fails every time: 1 initial attempt + 3 retries = 4 total calls
        self.mock_models.generate_content.side_effect = err503

        history = [types.Content(role="user", parts=[types.Part(text="test")])]
        with self.assertRaises(errors.ServerError):
            self.client.generate(history)

        self.assertEqual(self.mock_models.generate_content.call_count, 4)

    def test_non_transient_error_raises_immediately_without_retrying(self):
        err400 = errors.ClientError(
            400,
            {
                "error": {
                    "code": 400,
                    "message": "Bad request",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
        self.mock_models.generate_content.side_effect = err400

        history = [types.Content(role="user", parts=[types.Part(text="test")])]
        with self.assertRaises(errors.ClientError):
            self.client.generate(history)

        # Must not retry for 400
        self.assertEqual(self.mock_models.generate_content.call_count, 1)


class TestGroqAgentClient(unittest.TestCase):
    def setUp(self):
        self.client = GroqAgentClient(
            api_key="gsk_test_api_key",
            system_prompt="System prompt test",
            model="llama-3.3-70b-versatile",
            max_retries=2,
            initial_retry_delay=0.001,
            backoff_factor=1.5,
        )
        self.client._client = MagicMock()
        self.mock_completions = self.client._client.chat.completions

    def test_history_to_messages_conversion(self):
        history = [
            types.Content(role="user", parts=[types.Part(text="install vim")]),
            types.Content(
                role="model",
                parts=[
                    types.Part(text="I will install vim."),
                    types.Part(
                        function_call=types.FunctionCall(
                            name="propose_shell_command",
                            args={"command": "pacman -S vim", "explanation": "Install vim", "requires_root": True},
                            id="call_001",
                        )
                    ),
                ],
            ),
            types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id="call_001",
                            name="propose_shell_command",
                            response={"output": "success"},
                        )
                    )
                ],
            ),
        ]

        messages = self.client._history_to_messages(history)
        self.assertEqual(len(messages), 4)

        # 0: System prompt
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"], "System prompt test")

        # 1: User turn
        self.assertEqual(messages[1]["role"], "user")
        self.assertEqual(messages[1]["content"], "install vim")

        # 2: Assistant turn with tool call
        self.assertEqual(messages[2]["role"], "assistant")
        self.assertEqual(messages[2]["content"], "I will install vim.")
        self.assertEqual(len(messages[2]["tool_calls"]), 1)
        self.assertEqual(messages[2]["tool_calls"][0]["id"], "call_001")
        self.assertEqual(messages[2]["tool_calls"][0]["function"]["name"], "propose_shell_command")

        # 3: Tool response
        self.assertEqual(messages[3]["role"], "tool")
        self.assertEqual(messages[3]["tool_call_id"], "call_001")
        self.assertEqual(messages[3]["content"], "success")

    def test_generate_parses_response_and_tool_calls(self):
        mock_tool_call = MagicMock()
        mock_tool_call.id = "call_abc"
        mock_tool_call.function.name = "propose_shell_command"
        mock_tool_call.function.arguments = '{"command": "echo 1", "explanation": "test", "requires_root": false}'

        mock_msg = MagicMock()
        mock_msg.content = "Proposing command"
        mock_msg.tool_calls = [mock_tool_call]

        mock_choice = MagicMock()
        mock_choice.message = mock_msg
        mock_choice.finish_reason = "tool_calls"

        mock_resp = MagicMock()
        mock_resp.choices = [mock_choice]
        self.mock_completions.create.return_value = mock_resp

        history = [types.Content(role="user", parts=[types.Part(text="run echo")])]
        gen_resp = self.client.generate(history)

        self.assertIsInstance(gen_resp, types.GenerateContentResponse)
        self.assertEqual(len(gen_resp.candidates), 1)
        cand = gen_resp.candidates[0]
        self.assertEqual(cand.finish_reason, types.FinishReason.STOP)

        # Verify extracted text and tool calls
        text = BaseLLMClient.extract_text(gen_resp)
        self.assertEqual(text, "Proposing command")

        fcs = BaseLLMClient.extract_function_calls(gen_resp)
        self.assertEqual(len(fcs), 1)
        self.assertEqual(fcs[0].name, "propose_shell_command")
        self.assertEqual(fcs[0].args["command"], "echo 1")
        self.assertEqual(fcs[0].id, "call_abc")

    def test_groq_retry_on_transient_error(self):
        import openai

        rate_limit_err = openai.RateLimitError(
            message="Rate limit reached",
            response=MagicMock(status_code=429, headers={}),
            body=None,
        )

        mock_msg = MagicMock(content="recovered", tool_calls=[])
        mock_choice = MagicMock(message=mock_msg, finish_reason="stop")
        mock_resp = MagicMock(choices=[mock_choice])

        self.mock_completions.create.side_effect = [rate_limit_err, mock_resp]

        history = [types.Content(role="user", parts=[types.Part(text="test")])]
        gen_resp = self.client.generate(history)

        self.assertEqual(self.mock_completions.create.call_count, 2)
        self.assertEqual(BaseLLMClient.extract_text(gen_resp), "recovered")


class TestCreateLLMClientFactory(unittest.TestCase):
    def test_create_groq_client(self):
        client = create_llm_client("groq", "gsk_key", model_name="qwen-2.5-coder-32b")
        self.assertIsInstance(client, GroqAgentClient)
        self.assertEqual(client._model, "qwen-2.5-coder-32b")

    def test_create_gemini_client(self):
        client = create_llm_client("gemini", "gemini_key", model_name="gemini-3.8-flash")
        self.assertIsInstance(client, GeminiAgentClient)
        self.assertEqual(client._model, "gemini-3.8-flash")

    def test_create_ollama_client(self):
        client = create_llm_client("ollama", "", model_name="qwen2.5-coder:7b")
        self.assertIsInstance(client, (GroqAgentClient, OpenAIAgentClient))
        self.assertEqual(client._model, "qwen2.5-coder:7b")
        self.assertIn("11434", str(client._client.base_url))

    def test_create_ollama_client_default_model(self):
        client = create_llm_client("ollama", "")
        self.assertIsInstance(client, (GroqAgentClient, OpenAIAgentClient))
        self.assertEqual(client._model, OLLAMA_DEFAULT_MODEL)

    def test_create_openrouter_client(self):
        client = create_llm_client("openrouter", "sk-or-v1-key")
        self.assertIsInstance(client, OpenAIAgentClient)
        self.assertEqual(client._model, PROVIDER_CONFIGS["openrouter"]["default_model"])
        self.assertIn("openrouter.ai", str(client._client.base_url))

    def test_create_cerebras_client(self):
        client = create_llm_client("cerebras", "csk-key")
        self.assertIsInstance(client, OpenAIAgentClient)
        self.assertEqual(client._model, PROVIDER_CONFIGS["cerebras"]["default_model"])
        self.assertIn("cerebras.ai", str(client._client.base_url))

    def test_create_mistral_client(self):
        client = create_llm_client("mistral", "mistral-key-1234567890123456789012")
        self.assertIsInstance(client, OpenAIAgentClient)
        self.assertEqual(client._model, PROVIDER_CONFIGS["mistral"]["default_model"])
        self.assertIn("mistral.ai", str(client._client.base_url))

    def test_create_all_five_providers_client_instantiation(self):
        for provider in ("groq", "gemini", "openrouter", "cerebras", "mistral"):
            client = create_llm_client(provider, "test-key-1234567890123456789012")
            self.assertIsInstance(client, BaseLLMClient)
            expected_model = PROVIDER_CONFIGS[provider]["default_model"]
            self.assertEqual(client._model, expected_model)

    def test_create_invalid_provider_raises(self):
        with self.assertRaises(ValueError):
            create_llm_client("unknown_provider", "key")


class TestFetchAvailableModels(unittest.TestCase):
    def test_fetch_groq_models_success_and_filtering(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client

            m1 = MagicMock(id="llama-3.3-70b-versatile")
            m2 = MagicMock(id="whisper-large-v3")
            m3 = MagicMock(id="distil-whisper-large-v3-en")
            m4 = MagicMock(id="text-embedding-3-small")
            m5 = MagicMock(id="qwen-2.5-coder-32b")
            mock_client.models.list.return_value = MagicMock(data=[m1, m2, m3, m4, m5])

            models = fetch_available_models("groq", "test-key")
            self.assertEqual(models, ["llama-3.3-70b-versatile", "qwen-2.5-coder-32b"])

    def test_fetch_gemini_models_success_and_filtering(self):
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            m1 = MagicMock(supported_actions=["generateContent"])
            m1.name = "models/gemini-2.5-flash"
            m2 = MagicMock(supported_actions=["embedContent"])
            m2.name = "models/text-embedding-004"
            m3 = MagicMock(supported_actions=["generateContent"])
            m3.name = "models/gemini-2.0-flash"
            mock_client.models.list.return_value = [m1, m2, m3]

            models = fetch_available_models("gemini", "test-key")
            self.assertEqual(models, ["gemini-2.5-flash", "gemini-2.0-flash"])

    def test_fetch_groq_models_network_failure_falls_back(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.models.list.side_effect = ConnectionError("Network unreachable")

            models = fetch_available_models("groq", "test-key")
            self.assertIsInstance(models, list)
            self.assertIn("llama-3.3-70b-versatile", models)

    def test_fetch_gemini_models_network_failure_falls_back(self):
        with patch("google.genai.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client
            mock_client.models.list.side_effect = ConnectionError("Network unreachable")

            models = fetch_available_models("gemini", "test-key")
            self.assertIsInstance(models, list)
            self.assertIn("gemini-2.5-flash", models)

    def test_fetch_models_empty_key_returns_fallback(self):
        models = fetch_available_models("groq", "")
        self.assertIsInstance(models, list)
        self.assertIn("llama-3.3-70b-versatile", models)

    def test_fetch_models_none_or_empty_key_returns_fallback_all_providers(self):
        for provider, config in PROVIDER_CONFIGS.items():
            models_empty = fetch_available_models(provider, "")
            self.assertEqual(models_empty, config["fallback_models"])

            models_none = fetch_available_models(provider, None)
            self.assertEqual(models_none, config["fallback_models"])

    def test_fetch_openrouter_models_free_filtering(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client

            m1 = MagicMock(id="meta-llama/llama-3.3-70b-instruct:free")
            m2 = MagicMock(id="openai/gpt-4o")
            m3 = MagicMock(id="qwen/qwen-2.5-coder-32b-instruct:free")
            m4 = MagicMock(id="anthropic/claude-3.5-sonnet")
            mock_client.models.list.return_value = MagicMock(data=[m1, m2, m3, m4])

            models = fetch_available_models("openrouter", "sk-or-v1-valid-key-12345678901234")
            self.assertEqual(
                models,
                [
                    "meta-llama/llama-3.3-70b-instruct:free",
                    "qwen/qwen-2.5-coder-32b-instruct:free",
                ],
            )

    def test_fetch_ollama_models_success(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            m1 = MagicMock(id="qwen2.5-coder:7b")
            m2 = MagicMock(id="llama3.2:3b")
            mock_client.models.list.return_value = MagicMock(data=[m1, m2])

            models = fetch_available_models("ollama", "")
            self.assertEqual(models, ["qwen2.5-coder:7b", "llama3.2:3b"])
            mock_openai_cls.assert_called_once_with(
                base_url="http://127.0.0.1:11434/v1", api_key="ollama", timeout=3.0
            )

    def test_fetch_ollama_models_empty_falls_back(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.models.list.return_value = MagicMock(data=[])

            models = fetch_available_models("ollama", "")
            self.assertEqual(models, ["qwen2.5-coder:7b", "llama3.2:3b", "mistral:7b"])

    def test_fetch_ollama_models_failure_falls_back(self):
        with patch("openai.OpenAI") as mock_openai_cls:
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.models.list.side_effect = ConnectionError("Connection refused")

            models = fetch_available_models("ollama", "")
            self.assertEqual(models, ["qwen2.5-coder:7b", "llama3.2:3b", "mistral:7b"])


if __name__ == "__main__":
    unittest.main()
