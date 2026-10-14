import re
from dataclasses import dataclass
from typing import List

from openai import APIConnectionError, APIStatusError, OpenAI

from config import settings
from generation.prompt_builder import PromptMessages


@dataclass
class Answer:
    text: str
    citations: List[str]   # extracted citation strings from the answer text
    query: str
    chunks_used: int


def generate(prompt: PromptMessages, query: str, chunks_used: int) -> Answer:
    """
    Call the OpenAI API with the prompt messages.
    Parse citation strings from the response (lines matching '[Source: ..., Section: ...]').
    Return an Answer dataclass.
    Uses the configured OPENAI_MODEL and completion token budget.
    Read OPENAI_API_KEY from environment variables using python-dotenv.
    """
    settings.validate_configuration()
    if chunks_used == 0:
        return Answer(settings.REFUSAL_TEXT, [], query, 0)
    try:
        with OpenAI(api_key=settings.OPENAI_API_KEY,
                    timeout=settings.OPENAI_TIMEOUT_SECONDS,
                    max_retries=settings.OPENAI_MAX_RETRIES) as client:
            response = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                max_completion_tokens=settings.OPENAI_MAX_COMPLETION_TOKENS,
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
            )
    except APIStatusError as exc:
        code = getattr(exc, "code", None)
        safe_code = code if isinstance(code, str) and re.fullmatch(r"[a-zA-Z0-9_]+", code) else "provider_error"
        raise RuntimeError(f"OpenAI request failed (HTTP {exc.status_code}, {safe_code}). Check API access, billing and model configuration.") from None
    except APIConnectionError:
        raise RuntimeError("OpenAI connection failed. Check network access.") from None

    if not response.choices or response.choices[0].finish_reason != "stop":
        raise RuntimeError("OpenAI did not return a complete answer. Check the completion token budget.")
    text = response.choices[0].message.content or ""
    if not text.strip():
        raise RuntimeError("OpenAI returned an empty answer.")
    citations = re.findall(r'\[Source: .+?, Section: .+?\]', text)
    if text.strip().strip("'\"") == settings.REFUSAL_TEXT:
        text, citations = settings.REFUSAL_TEXT, []
    elif not citations or any(citation not in prompt.allowed_citations for citation in citations):
        raise RuntimeError("Generated answer has missing or ungrounded source citations.")

    return Answer(
        text=text,
        citations=citations,
        query=query,
        chunks_used=chunks_used,
    )
