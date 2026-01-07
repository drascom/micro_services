#!/usr/bin/env python3
"""
Script: Extract Medical Data via LLM
Purpose: Use LLM to extract structured medical data from normalized questionnaire text
Usage: python execution/extract_medical_data.py <cleaned_text>

Inputs:
    - cleaned_text: Normalized text from a medical questionnaire (via stdin or argument)

Outputs:
    - JSON object with extracted medical data matching the preop schema
"""

import json
import sys
import re
from typing import Optional, Any
from dataclasses import dataclass, asdict, field

from config import get_llm_config, LLMConfig


# Medical data extraction prompt - Hair Transplant Pre-Op Questionnaire
EXTRACTION_PROMPT = """You are a medical data extraction system for hair transplant clinic pre-operative questionnaires.

CRITICAL RULES:
1. Extract ONLY information explicitly present in the document
2. Do NOT fabricate, infer, or guess missing data
3. If a field is not present or unclear, omit it from output
4. Interpret checkbox markers: [CHECKED] means Yes/selected, [UNCHECKED] means No/not selected
5. For Yes/No questions: output "yes" or "no" (lowercase)
6. Preserve exact values as written (names, dates, phone numbers, etc.)
7. Output ONLY valid JSON, no additional text

CHECKBOX INTERPRETATION:
- [CHECKED] next to "Yes" = "yes"
- [CHECKED] next to "No" = "no"
- [UNCHECKED] = not selected
- For multi-select emotional wellbeing questions, include all checked items in array

REQUIRED OUTPUT SCHEMA:
{{
  "record_valid": true/false,
  "full_name": "full name as written",
  "first_name": "first name only",
  "last_name": "last/surname only",
  "dob": "date of birth as written (e.g., 10/11/1976)",
  "email": "email address",
  "phone": "phone number",
  "address": "full address",
  "gp_name": "GP/doctor name",
  "gp_address": "GP surgery address",
  "gp_phone": "GP phone number",
  "emergency_contact": {{
    "name": "emergency contact name",
    "number": "emergency contact phone"
  }},
  "preop_answers": {{
    "local_anaesthetic_before": "yes/no",
    "hair_transplant_before": "yes/no",
    "cardiovascular_problems": "yes/no or details if provided",
    "diabetes": "yes/no or details",
    "blood_borne_disease_hiv_hepatitis": "yes/no",
    "keloid_scars_prone": "yes/no",
    "skin_or_scalp_disorders": "yes/no or condition name",
    "ulcer_or_stomach_problems": "yes/no",
    "sickle_cell_or_traits": "yes/no",
    "aspirin_or_blood_thinners": "yes/no",
    "smoking_alcohol_recreational_drugs": "yes/no or frequency/details",
    "vitamin_supplements_regular": "yes/no or list",
    "additional_details": "any additional info provided",
    "family_history": "yes/no or details of baldness/hair loss in family",
    "surgical_operations": ["list of previous surgeries"] or "no",
    "allergies": ["list of allergies"] or "no",
    "medications": ["list of current medications"] or "no",
    "height": "height as written",
    "weight": "weight as written",
    "high_blood_pressure": "yes/no",
    "emotional_wellbeing": ["anxiety", "depression", "insecure", "affecting life", "unhappy", etc.]
  }}
}}

FIELD MAPPING GUIDE:
- "Have you had local anaesthetic before" → local_anaesthetic_before
- "Have you had a hair transplant before" → hair_transplant_before
- "Heart/cardiovascular problems" → cardiovascular_problems
- "Diabetes" → diabetes
- "HIV/Hepatitis/blood borne disease" → blood_borne_disease_hiv_hepatitis
- "Prone to keloid scarring" → keloid_scars_prone
- "Skin or scalp conditions/disorders" → skin_or_scalp_disorders
- "Stomach ulcers/problems" → ulcer_or_stomach_problems
- "Sickle cell/traits" → sickle_cell_or_traits
- "Taking aspirin/blood thinners" → aspirin_or_blood_thinners
- "Smoking/alcohol/recreational drugs" → smoking_alcohol_recreational_drugs
- "Vitamins/supplements" → vitamin_supplements_regular
- "Family history of hair loss/baldness" → family_history
- "Previous operations/surgeries" → surgical_operations
- "Allergies" → allergies
- "Current medications" → medications
- "Blood pressure" → high_blood_pressure
- "How does hair loss make you feel" / emotional checkboxes → emotional_wellbeing

For preop_answers:
- Use "yes" or "no" for simple boolean answers
- If details are provided with a "yes", include the details as the value
- For lists (allergies, medications, surgeries), use arrays
- If answered "no" or "none", you can use "no" or an empty array []
- For emotional_wellbeing, always use an array of selected feelings

Only include fields that have actual data. Set record_valid to true if full_name and dob are present.

DOCUMENT TEXT:
---
{document_text}
---

Extract the structured data now. Output only the JSON object:"""


@dataclass
class ExtractionResult:
    """Result of medical data extraction."""
    status: str
    data: Optional[dict] = None
    is_valid: bool = False
    validation_errors: list = field(default_factory=list)
    message: Optional[str] = None


def call_openai(prompt: str, config: LLMConfig) -> str:
    """Call OpenAI API for extraction."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")

    client = OpenAI(api_key=config.api_key)

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise medical data extraction system for hair transplant pre-operative questionnaires. Output only valid JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        response_format={"type": "json_object"}
    )

    return response.choices[0].message.content or ""


def call_anthropic(prompt: str, config: LLMConfig) -> str:
    """Call Anthropic API for extraction."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("anthropic package not installed. Run: pip install anthropic")

    client = anthropic.Anthropic(api_key=config.api_key)

    response = client.messages.create(
        model=config.model,
        max_tokens=config.max_tokens,
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=config.temperature,
    )

    # Extract text from response
    return response.content[0].text if response.content else ""


def call_ollama(prompt: str, config: LLMConfig) -> str:
    """Call Ollama API for extraction (OpenAI-compatible endpoint)."""
    try:
        from openai import OpenAI
    except ImportError:
        raise ImportError("openai package not installed. Run: pip install openai")

    # Ollama uses OpenAI-compatible API
    client = OpenAI(
        base_url=f"{config.base_url}/v1",
        api_key=config.api_key or "ollama"  # Ollama doesn't require real API key
    )

    response = client.chat.completions.create(
        model=config.model,
        messages=[
            {
                "role": "system",
                "content": "You are a precise medical data extraction system for hair transplant pre-operative questionnaires. Output only valid JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=config.temperature,
        max_tokens=config.max_tokens,
    )

    return response.choices[0].message.content or ""


def parse_json_response(response: str) -> dict:
    """Parse JSON from LLM response, handling common issues."""
    # Try direct parse first
    try:
        return json.loads(response)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code blocks
    json_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", response)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find JSON object in response
    json_match = re.search(r"\{[\s\S]*\}", response)
    if json_match:
        try:
            return json.loads(json_match.group(0))
        except json.JSONDecodeError:
            pass

    raise ValueError("Could not parse JSON from LLM response")


def normalize_value(value: Any) -> Any:
    """Normalize extracted values - lowercase strings, clean whitespace."""
    if isinstance(value, str):
        cleaned = value.strip().lower()
        # Keep certain values as-is (emails, phones, addresses contain mixed case/numbers)
        if "@" in value or any(c.isdigit() for c in value):
            return value.strip()
        return cleaned
    return value


def clean_extracted_data(data: dict) -> dict:
    """Clean extracted data - remove nulls, normalize, but keep structure."""
    if not isinstance(data, dict):
        return data

    cleaned = {}

    # Fields that should preserve original case
    preserve_case_fields = {"email", "phone", "gp_phone", "number", "address",
                           "gp_address", "dob", "height", "weight", "additional_details"}

    for key, value in data.items():
        # Skip null/None values
        if value is None:
            continue

        # Skip empty strings
        if isinstance(value, str) and not value.strip():
            continue

        # Handle nested dicts (emergency_contact, preop_answers)
        if isinstance(value, dict):
            cleaned_nested = clean_extracted_data(value)
            if cleaned_nested:
                cleaned[key] = cleaned_nested
            continue

        # Handle lists (allergies, medications, emotional_wellbeing)
        if isinstance(value, list):
            cleaned_list = []
            for item in value:
                if isinstance(item, dict):
                    cleaned_item = clean_extracted_data(item)
                    if cleaned_item:
                        cleaned_list.append(cleaned_item)
                elif isinstance(item, str):
                    stripped = item.strip()
                    if stripped and stripped.lower() not in ["n/a", "na", "none", "null", "-", "—"]:
                        cleaned_list.append(stripped.lower())
                elif item is not None:
                    cleaned_list.append(item)

            # Keep empty lists for certain fields (shows "no items")
            cleaned[key] = cleaned_list
            continue

        # Handle strings
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue

            # Preserve case for certain fields
            if key in preserve_case_fields:
                cleaned[key] = stripped
            else:
                cleaned[key] = stripped.lower()
            continue

        # Keep booleans and other types as-is
        cleaned[key] = value

    return cleaned


def validate_extracted_data(data: dict) -> tuple[bool, list[str]]:
    """
    Validate extracted medical data for required fields.

    Returns:
        Tuple of (is_valid, list of validation errors)
    """
    errors = []

    # Required: full name
    if not data.get("full_name"):
        errors.append("Missing required field: full_name")

    # Required: date of birth
    if not data.get("dob"):
        errors.append("Missing required field: dob")

    is_valid = len(errors) == 0
    return is_valid, errors


def extract_medical_data(
    document_text: str,
    config: Optional[LLMConfig] = None,
    retry_count: int = 1
) -> ExtractionResult:
    """
    Extract structured medical data from questionnaire text using LLM.

    Args:
        document_text: Normalized questionnaire text
        config: LLM configuration (uses default if None)
        retry_count: Number of retries on failure

    Returns:
        ExtractionResult with extracted data
    """
    if config is None:
        config = get_llm_config()

    # Ollama may not require API key
    if config.provider != "ollama" and not config.api_key:
        return ExtractionResult(
            status="error",
            message=f"{config.provider.upper()}_API_KEY is not configured"
        )

    if not document_text or len(document_text.strip()) < 50:
        return ExtractionResult(
            status="error",
            message="Document text is too short or empty for meaningful extraction"
        )

    # Prepare prompt
    prompt = EXTRACTION_PROMPT.format(document_text=document_text)

    last_error = None
    for attempt in range(retry_count + 1):
        try:
            # Call appropriate LLM
            if config.provider == "openai":
                response = call_openai(prompt, config)
            elif config.provider == "anthropic":
                response = call_anthropic(prompt, config)
            elif config.provider == "ollama":
                response = call_ollama(prompt, config)
            else:
                return ExtractionResult(
                    status="error",
                    message=f"Unknown LLM provider: {config.provider}"
                )

            # Parse response
            data = parse_json_response(response)

            # Clean the data
            cleaned_data = clean_extracted_data(data)

            # Validate
            is_valid, validation_errors = validate_extracted_data(cleaned_data)

            # Set record_valid flag based on validation
            cleaned_data["record_valid"] = is_valid

            return ExtractionResult(
                status="success",
                data=cleaned_data,
                is_valid=is_valid,
                validation_errors=validation_errors
            )

        except ImportError as e:
            return ExtractionResult(
                status="error",
                message=str(e)
            )

        except json.JSONDecodeError as e:
            last_error = f"Failed to parse LLM response as JSON: {str(e)}"
            continue

        except Exception as e:
            last_error = f"LLM extraction error: {str(e)}"
            continue

    return ExtractionResult(
        status="error",
        message=last_error or "Extraction failed after retries"
    )


def main():
    """Main entry point."""
    # Read from stdin if no argument provided
    if len(sys.argv) < 2:
        if sys.stdin.isatty():
            print(json.dumps({
                "status": "error",
                "message": "Usage: python extract_medical_data.py <document_text> or pipe via stdin"
            }))
            sys.exit(1)
        document_text = sys.stdin.read()
    else:
        document_text = sys.argv[1]

    result = extract_medical_data(document_text)

    # Output the result
    if result.status == "success" and result.data:
        # Output just the data for successful extraction
        print(json.dumps(result.data, indent=2, ensure_ascii=False))
    else:
        output = asdict(result)
        print(json.dumps(output, indent=2, ensure_ascii=False))

    if result.status == "error":
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
