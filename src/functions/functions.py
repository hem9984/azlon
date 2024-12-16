# src/functions/functions.py
from restack_ai.function import function, log
from dataclasses import dataclass
import os
import openai
import json
from dotenv import load_dotenv
import tempfile
import subprocess

from pydantic import BaseModel
from typing import List, Optional

load_dotenv()

openai.api_key = os.environ.get("OPENAI_KEY")

# Use the OpenAI Python SDK's structured output parsing
from openai import OpenAI
client = OpenAI(api_key=openai.api_key)

class FileItem(BaseModel):
    filename: str
    content: str

    class Config:
        extra = "forbid"
        schema_extra = {
            "type": "object",
            "properties": {
                "filename": {"type": "string"},
                "content": {"type": "string"}
            },
            "required": ["filename", "content"],
            "additionalProperties": False
        }

class GenerateCodeSchema(BaseModel):
    dockerfile: str
    files: List[FileItem]
    
    class Config:
        extra = "forbid"
        schema_extra = {
            "type": "object",
            "properties": {
                "dockerfile": {"type": "string"},
                "files": {
                    "type": "array",
                    "items": {"$ref": "#/$defs/FileItem"}
                }
            },
            "required": ["dockerfile", "files"],
            "additionalProperties": False,
            "$defs": {
                "FileItem": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["filename", "content"],
                    "additionalProperties": False
                }
            }
        }

class ValidateOutputSchema(BaseModel):
    result: bool
    dockerfile: Optional[str] = None
    files: Optional[List[FileItem]] = None
    
    class Config:
        extra = "forbid"
        schema_extra = {
            "type": "object",
            "properties": {
                "result": {"type": "boolean"},
                "dockerfile": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "null"}
                    ]
                },
                "files": {
                    "anyOf": [
                        {
                            "type": "array",
                            "items": {"$ref": "#/$defs/FileItem"}
                        },
                        {"type": "null"}
                    ]
                }
            },
            "required": ["result", "dockerfile", "files"],
            "additionalProperties": False,
            "$defs": {
                "FileItem": {
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string"},
                        "content": {"type": "string"}
                    },
                    "required": ["filename", "content"],
                    "additionalProperties": False
                }
            }
        }


@dataclass
class GenerateCodeInput:
    user_prompt: str
    test_conditions: str

@dataclass
class GenerateCodeOutput:
    dockerfile: str
    files: list

@function.defn()
async def generate_code(input: GenerateCodeInput) -> GenerateCodeOutput:
    log.info("generate_code started", input=input)

    prompt = f"""
    You are an autonomous coding agent.

The user prompt: {input.user_prompt}
The test conditions: {input.test_conditions}

You must produce a Docker environment and code that meets the user's test conditions.

**Additional Requirements**:
- Start by creating a `readme.md` file as your first file in the files array. This `readme.md` should begin with `#./readme.md` and contain:
  - A brief summary of the user's prompt.
  - A brief step-by-step plan of what you intend to do to meet the test conditions.
- Use a stable base Docker image: `FROM python:3.10-slim`.
- Install any necessary dependencies in the Dockerfile.
- Generate any configuration files (like `pyproject.toml` or `requirements.txt`) before the main Python files, if needed.
- Each file must start with `#./<filename>` on the first line. For example:
  `#./main.py`
  `print('hello world')`
- The Dockerfile should define an ENTRYPOINT that runs the main script or commands automatically so that running the container (e.g. `docker run ...`) immediately produces the final output required by the test conditions.
- Ensure the output visible on stdout fulfills the test conditions without further intervention.

**Return JSON strictly matching this schema**:
{{
  "dockerfile": "<string>",
  "files": [
    {{
      "filename": "<string>",
      "content": "<string>"
    }},
    ...
  ]
}}

**Order of files**:
1. `readme.md` (with reasoning and plan)
2. Any configuration files (like `pyproject.toml` or `requirements.txt`)
3. Your main Python application files

**Example**:
{{
  "dockerfile": "FROM python:3.10-slim\\n... ENTRYPOINT [\\"python3\\", \\"main.py\\"]",
  "files": [
    {{
      "filename": "readme.md",
      "content": "#./readme.md\\nThis is my reasoning..."
    }},
    {{
      "filename": "pyproject.toml",
      "content": "#./pyproject.toml\\n..."
    }},
    {{
      "filename": "main.py",
      "content": "#./main.py\\nprint('hello world')"
    }}
  ]
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

    files_list = [{"filename": f.filename, "content": f.content} for f in data.files]

    return GenerateCodeOutput(dockerfile=data.dockerfile, files=files_list)


@dataclass
class RunCodeInput:
    dockerfile: str
    files: list

@dataclass
class RunCodeOutput:
    output: str

@function.defn()
async def run_locally(input: RunCodeInput) -> RunCodeOutput:
    log.info("run_locally started", input=input)
    
    with tempfile.TemporaryDirectory() as temp_dir:
        dockerfile_path = os.path.join(temp_dir, "Dockerfile")
        
        # Write the Dockerfile
        with open(dockerfile_path, "w", encoding="utf-8") as f:
            f.write(input.dockerfile)
        
        # Write each file
        for file_item in input.files:
            file_path = os.path.join(temp_dir, file_item["filename"])
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w", encoding="utf-8") as ff:
                ff.write(file_item["content"])
        
        # Build the Docker image
        build_cmd = ["docker", "build", "-t", "myapp", temp_dir]
        build_process = subprocess.run(build_cmd, capture_output=True, text=True)
        if build_process.returncode != 0:
            return RunCodeOutput(output=build_process.stderr or build_process.stdout)
        
        # Run the Docker container
        run_cmd = ["docker", "run", "--rm", "myapp"]
        run_process = subprocess.run(run_cmd, capture_output=True, text=True)
        if run_process.returncode != 0:
            return RunCodeOutput(output=run_process.stderr or run_process.stdout)
        
        return RunCodeOutput(output=run_process.stdout)


@dataclass
class ValidateOutputInput:
    dockerfile: str
    files: list
    output: str
    test_conditions: str

@dataclass
class ValidateOutputOutput:
    result: bool
    dockerfile: Optional[str] = None
    files: Optional[list] = None

@function.defn()
async def validate_output(input: ValidateOutputInput) -> ValidateOutputOutput:
    log.info("validate_output started", input=input)

    files_str = json.dumps(input.files, indent=2)

    validation_prompt = f"""
    The test conditions: {input.test_conditions}

dockerfile:
{input.dockerfile}

files:
{files_str}

output:
{input.output}

If all test conditions are met, return exactly:
{{ "result": true, "dockerfile": null, "files": null }}

Otherwise (if you need to fix or add files, modify the dockerfile, etc.), return exactly:
{{
  "result": false,
  "dockerfile": "FROM python:3.10-slim\\n...",
  "files": [
    {{
      "filename": "filename.ext",
      "content": "#./filename.ext\\n..."
    }}
  ]
}}

You may add, remove, or modify multiple files as needed when returning false. Just ensure you follow the same schema and format strictly. Do not add extra commentary or keys.
If returning null for dockerfile or files, use JSON null, not a string.
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
        return ValidateOutputOutput(result=False)

    data = result.parsed

    updated_files = [{"filename": f.filename, "content": f.content} for f in data.files] if data.files is not None else None

    return ValidateOutputOutput(result=data.result, dockerfile=data.dockerfile, files=updated_files)

