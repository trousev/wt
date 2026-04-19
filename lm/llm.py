import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-4-5",
    "openai": "gpt-4o",
    "openrouter": "openrouter/anthropic/claude-3.5-sonnet",
    "ollama": "llama3",
}

PROVIDER_URLS = {
    "anthropic": "https://api.anthropic.com/v1/messages",
    "openai": "https://api.openai.com/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
    "ollama": None,
}


def get_provider():
    provider = os.environ.get("LM_PROVIDER", "anthropic").lower()
    if provider not in DEFAULT_MODELS:
        print(
            f'Error: Unknown provider "{provider}". Must be one of: {", ".join(DEFAULT_MODELS.keys())}',
            file=sys.stderr,
        )
        sys.exit(1)
    return provider


def get_model(provider):
    model = os.environ.get("LM_MODEL")
    if not model:
        model = DEFAULT_MODELS.get(provider, "llama3")
    return model


def get_api_key(provider):
    api_key = os.environ.get("LM_API_KEY")
    if not api_key:
        print("Error: LM_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return api_key


def get_ollama_url():
    return os.environ.get("LM_OLLAMA_URL", "http://localhost:11434")


def call_llm_api(prompt, system_prompt=None, model=None):
    return call_llm_api_with_messages(prompt, system_prompt, model, None)


def call_llm_api_with_messages(prompt, system_prompt, model, messages):
    provider = get_provider()

    if model is None:
        model = get_model(provider)

    if provider == "ollama":
        return call_ollama_api_with_messages(prompt, system_prompt, model, messages)

    api_key = get_api_key(provider)

    if provider == "anthropic":
        return call_anthropic_api_with_messages(prompt, system_prompt, model, api_key, messages)
    elif provider == "openai":
        return call_openai_api_with_messages(prompt, system_prompt, model, api_key, messages)
    elif provider == "openrouter":
        return call_openrouter_api_with_messages(prompt, system_prompt, model, api_key, messages)

    return None


def call_anthropic_api(prompt, system_prompt, model, api_key):
    return call_anthropic_api_with_messages(prompt, system_prompt, model, api_key, None)


def call_anthropic_api_with_messages(prompt, system_prompt, model, api_key, messages):
    url = "https://api.anthropic.com/v1/messages"

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    if messages:
        all_messages = messages + [{"role": "user", "content": prompt}]
    else:
        all_messages = [{"role": "user", "content": prompt}]

    data = {
        "model": model,
        "max_tokens": 4096,
        "system": system_prompt,
        "messages": all_messages,
    }

    return _make_request(url, data, headers)


def call_openai_api(prompt, system_prompt, model, api_key):
    return call_openai_api_with_messages(prompt, system_prompt, model, api_key, None)


def call_openai_api_with_messages(prompt, system_prompt, model, api_key, messages):
    url = "https://api.openai.com/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }

    all_messages = []
    if system_prompt:
        all_messages.append({"role": "system", "content": system_prompt})
    if messages:
        all_messages.extend(messages)
    all_messages.append({"role": "user", "content": prompt})

    data = {
        "model": model,
        "max_tokens": 4096,
        "messages": all_messages,
    }

    return _make_request(url, data, headers)


def call_openrouter_api(prompt, system_prompt, model, api_key):
    return call_openrouter_api_with_messages(prompt, system_prompt, model, api_key, None)


def call_openrouter_api_with_messages(prompt, system_prompt, model, api_key, messages):
    url = "https://openrouter.ai/api/v1/chat/completions"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/anomalyco/lm",
        "X-Title": "LM",
    }

    all_messages = []
    if system_prompt:
        all_messages.append({"role": "system", "content": system_prompt})
    if messages:
        all_messages.extend(messages)
    all_messages.append({"role": "user", "content": prompt})

    data = {
        "model": model,
        "max_tokens": 4096,
        "messages": all_messages,
    }

    return _make_request(url, data, headers)


def call_ollama_api(prompt, system_prompt, model):
    return call_ollama_api_with_messages(prompt, system_prompt, model, None)


def call_ollama_api_with_messages(prompt, system_prompt, model, messages):
    base_url = get_ollama_url()
    url = f"{base_url}/api/chat"

    headers = {"Content-Type": "application/json"}

    all_messages = []
    if system_prompt:
        all_messages.append({"role": "system", "content": system_prompt})
    if messages:
        all_messages.extend(messages)
    all_messages.append({"role": "user", "content": prompt})

    data = {
        "model": model,
        "messages": all_messages,
        "stream": False,
    }

    try:
        json_data = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(url, data=json_data, headers=headers)
        with urllib.request.urlopen(request) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if "message" in response_data and "content" in response_data["message"]:
            return response_data["message"]["content"]
        else:
            print("Error: Unexpected response format from Ollama", file=sys.stderr)
            return None

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        print(f"Error response: {error_body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def generate_image(
    prompt: str,
    api_key: str,
    model: str = "gpt-image-1",
    size: str = "1024x1024",
    quality: str = "low",
) -> bytes | None:
    """Generate an image via OpenAI's image generation API. Returns raw PNG bytes."""
    url = "https://api.openai.com/v1/images/generations"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    data = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
        "output_format": "png",
    }
    try:
        json_data = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(url, data=json_data, headers=headers)
        with urllib.request.urlopen(request) as response:
            response_data = json.loads(response.read().decode("utf-8"))
        b64 = response_data["data"][0]["b64_json"]
        return base64.b64decode(b64)
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        try:
            error_data = json.loads(error_body)
            if "error" in error_data:
                print(
                    f"API Error: {error_data['error'].get('message', 'Unknown error')}",
                    file=sys.stderr,
                )
        except json.JSONDecodeError:
            print(f"Error response: {error_body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def _make_request(url, data, headers):
    try:
        json_data = json.dumps(data).encode("utf-8")
        request = urllib.request.Request(url, data=json_data, headers=headers)
        with urllib.request.urlopen(request) as response:
            response_data = json.loads(response.read().decode("utf-8"))

        if "content" in response_data and response_data["content"]:
            return response_data["content"][0]["text"]
        elif "choices" in response_data and response_data["choices"]:
            return response_data["choices"][0]["message"]["content"]
        else:
            print("Error: Unexpected response format", file=sys.stderr)
            return None

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {e.reason}", file=sys.stderr)
        try:
            error_data = json.loads(error_body)
            if "error" in error_data:
                print(
                    f"API Error: {error_data['error'].get('message', 'Unknown error')}",
                    file=sys.stderr,
                )
        except json.JSONDecodeError:
            print(f"Error response: {error_body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None
