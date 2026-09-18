#!/usr/bin/env python3
"""Apply maintained full-input, usage, and bounded-retry patches to TTP-LLM."""

from __future__ import annotations

import argparse
from pathlib import Path


def replace_once(path: Path, before: str, after: str) -> None:
    source = path.read_text(encoding="utf-8-sig")
    if after in source:
        return
    if before not in source:
        raise RuntimeError(f"TTP-LLM patch target not found in {path}")
    path.write_text(source.replace(before, after, 1), encoding="utf-8")


def patch_prompt_only(path: Path) -> None:
    source = path.read_text(encoding="utf-8-sig")
    if "import json\n" not in source:
        replace_once(path, "import openai\nimport time", "import openai\nimport json\nimport time")
        source = path.read_text(encoding="utf-8-sig")
    if "import os\n" not in source or "import sys\n" not in source:
        replace_once(path, "import json\nimport time", "import json\nimport os\nimport sys\nimport time")
    replace_once(
        path,
        '        result = response.choices[0].message["content"]',
        '''        usage = response.get("usage") or {}
        usage_record = {
            "model": response.get("model", model),
            "prompt_tokens": int(usage.get("prompt_tokens", 0)),
            "completion_tokens": int(usage.get("completion_tokens", 0)),
            "total_tokens": int(usage.get("total_tokens", 0)),
        }
        # Machine-readable and intentionally excludes prompts, responses, and keys.
        print("TTPWB_OPENAI_USAGE " + json.dumps(usage_record, sort_keys=True), flush=True)
        result = response.choices[0].message["content"]''',
    )
    replace_once(
        path,
        "    df = pd.read_csv(csv_file)[:20]\n    for procedure",
        "    df = pd.read_csv(csv_file)\n    for procedure",
    )
    replace_once(
        path,
        "config.read('config.ini')\n\ndef get_completion",
        '''config.read('config.ini')

def _nonnegative_int_env(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value

_OPENAI_MAX_RETRIES = _nonnegative_int_env("TTPWB_OPENAI_MAX_RETRIES", 3)
_OPENAI_MAX_TOTAL_RETRIES = _nonnegative_int_env("TTPWB_OPENAI_MAX_TOTAL_RETRIES", 25)
_OPENAI_RETRY_TOTAL = 0

def _openai_error_metadata(error):
    body = getattr(error, "json_body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    headers = getattr(error, "headers", None) or {}
    wanted_headers = (
        "retry-after",
        "x-ratelimit-limit-requests",
        "x-ratelimit-remaining-requests",
        "x-ratelimit-reset-requests",
        "x-ratelimit-limit-tokens",
        "x-ratelimit-remaining-tokens",
        "x-ratelimit-reset-tokens",
    )
    normalized_headers = {str(key).lower(): str(value) for key, value in dict(headers).items()}
    return {
        "exception": type(error).__name__,
        "http_status": getattr(error, "http_status", None),
        "error_type": detail.get("type") if isinstance(detail, dict) else None,
        "error_code": detail.get("code") if isinstance(detail, dict) else None,
        "rate_limits": {
            key: normalized_headers[key]
            for key in wanted_headers
            if key in normalized_headers
        },
    }

def _retry_openai(error, request_failure):
    global _OPENAI_RETRY_TOTAL
    _OPENAI_RETRY_TOTAL += 1
    exhausted = (
        request_failure > _OPENAI_MAX_RETRIES
        or _OPENAI_RETRY_TOTAL > _OPENAI_MAX_TOTAL_RETRIES
    )
    delay = 0.0 if exhausted else min(30.0, 2.0 ** (request_failure - 1)) + random.uniform(0.0, 1.0)
    record = _openai_error_metadata(error)
    record.update({
        "action": "abort" if exhausted else "retry",
        "request_failure": request_failure,
        "max_retries": _OPENAI_MAX_RETRIES,
        "total_retries": _OPENAI_RETRY_TOTAL,
        "max_total_retries": _OPENAI_MAX_TOTAL_RETRIES,
        "delay_seconds": round(delay, 3),
    })
    # Do not include exception messages, request content, responses, or credentials.
    print("TTPWB_OPENAI_RETRY " + json.dumps(record, sort_keys=True), file=sys.stderr, flush=True)
    if exhausted:
        raise error
    time.sleep(delay)

def get_completion''',
    )
    replace_once(
        path,
        '''        while True:
            try:
                print(question)
                result = get_completion(prompt, model=model)
                print(result,'\\n')
                predictions.append(result)
                break
            except (openai.error.RateLimitError, openai.error.APIError, openai.error.Timeout,
                    openai.error.OpenAIError, openai.error.ServiceUnavailableError):
                delay = random.randint(2, 6)
                time.sleep(delay)''',
        '''        request_failures = 0
        while True:
            try:
                print(question)
                result = get_completion(prompt, model=model)
                print(result,'\\n')
                predictions.append(result)
                break
            except (openai.error.RateLimitError, openai.error.APIError, openai.error.Timeout,
                    openai.error.OpenAIError, openai.error.ServiceUnavailableError) as error:
                request_failures += 1
                _retry_openai(error, request_failures)''',
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    patch_prompt_only(args.path)


if __name__ == "__main__":
    main()
