# src/functions/functions.py
from restack_ai.function import function, log
from dataclasses import dataclass
import os
import openai
import json
from dotenv import load_dotenv

from e2b_code_interpreter import Sandbox
from pydantic import BaseModel
from typing import Dict, Union, Optional

load_dotenv()

openai.api_key = os.environ.get("OPENAI_KEY")

# Use the OpenAI Python SDK's structured output parsing
from openai import OpenAI
client = OpenAI(api_key=openai.api_key)

class GenerateCodeSchema(BaseModel):
    dockerfile: str
    files: Dict[str, str]

class ValidateOutputSchema(BaseModel):
    # We want result to always be present and boolean
    # dockerfile and files can be null if result = true
    result: bool
    dockerfile: Optional[str] = None
    files: Optional[Dict[str, str]] = None


@dataclass
class GenerateCodeInput:
    user_prompt: str
    test_conditions: str

@dataclass
class GenerateCodeOutput:
    dockerfile: str
    files: dict

@function.defn()
async def generate_code(input: GenerateCodeInput) -> GenerateCodeOutput:
    log.info("generate_code started", input=input)

    prompt = f"""
    You are an autonomous coding agent.

    The user prompt: {input.user_prompt}
    The test conditions: {input.test_conditions}

    Generate a Dockerfile starting with 'FROM e2bdev/code-interpreter:latest' and any necessary steps.
    Generate Python code files and possibly a pyproject.toml/requirements.txt.
    Each file must start with '#path/to/<filename>' on the first line.

    Return JSON strictly matching this schema:
    dockerfile: string
    files: object mapping filename->file_content

    Example:
    {{
       "dockerfile": "FROM e2bdev/code-interpreter:latest\\n...",
       "files": {{
          "main.py": "#path/to/main.py\\n ...",
          "pyproject.toml": "#path/to/pyproject.toml\\n ..."
       }}
    }}
    """

    completion = client.beta.chat.completions.parse(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": prompt}
        ],
        response_format=GenerateCodeSchema
    )

    result = completion.choices[0].message
    if result.refusal:
        raise RuntimeError("Model refused to generate code.")
    data = result.parsed

    return GenerateCodeOutput(dockerfile=data.dockerfile, files=data.files)


@dataclass
class RunCodeInput:
    dockerfile: str
    files: dict

@dataclass
class RunCodeOutput:
    output: str

@function.defn()
async def run_code_in_e2b(input: RunCodeInput) -> RunCodeOutput:
    log.info("run_code_in_e2b started", input=input)
    # Use a predefined template_id created from e2b template build
    template_id = os.environ.get("E2B_TEMPLATE_ID")  # ensure this is set
    sbx = Sandbox(template_id=template_id)

    # Write Dockerfile and files
    sbx.files.write("/home/user/Dockerfile", input.dockerfile.encode("utf-8"))
    for filename, content in input.files.items():
        sbx.files.write(f"/home/user/{filename}", content.encode("utf-8"))

    build = sbx.run_code("docker build -t myapp /home/user")
    if build.returncode != 0:
        return RunCodeOutput(output=build.logs)

    run = sbx.run_code("docker run --rm myapp")
    return RunCodeOutput(output=run.logs)


@dataclass
class ValidateOutputInput:
    dockerfile: str
    files: dict
    output: str
    test_conditions: str

@dataclass
class ValidateOutputOutput:
    result: bool
    dockerfile: Optional[str] = None
    files: Optional[dict] = None

@function.defn()
async def validate_output(input: ValidateOutputInput) -> ValidateOutputOutput:
    log.info("validate_output started", input=input)

    validation_prompt = f"""
    The test conditions: {input.test_conditions}

    dockerfile:
    {input.dockerfile}

    files:
    {json.dumps(input.files, indent=2)}

    output:
    {input.output}

    If all test conditions are met, return:
    {{ "result": true, "dockerfile": null, "files": null }}

    Otherwise, return:
    {{
      "result": false,
      "dockerfile": "FROM e2bdev/code-interpreter:latest\\n...",
      "files": {{
         "filename.ext": "#path/to/filename.ext\\n..."
      }}
    }}
    """

    completion = client.beta.chat.completions.parse(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": "You are a coding assistant."},
            {"role": "user", "content": validation_prompt}
        ],
        response_format=ValidateOutputSchema
    )

    result = completion.choices[0].message
    if result.refusal:
        # If the model refuses to validate, handle gracefully
        return ValidateOutputOutput(result=False)

    data = result.parsed
    return ValidateOutputOutput(result=data.result, dockerfile=data.dockerfile, files=data.files)
