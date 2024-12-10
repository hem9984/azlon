# Copyright (C) 2024 Harrison E. Muchnic
# This program is licensed under the Affero General Public License (AGPL).
# See the LICENSE file for details.


# src/functions/functions.py
from restack_ai.function import function, log
from dataclasses import dataclass
import os
import openai
import json
from dotenv import load_dotenv

from e2b_code_interpreter import Sandbox
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
    files: list  # now a list of FileItem-like dicts

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
    files: array of objects with "filename" and "content" keys

    Example:
    {{
       "dockerfile": "FROM e2bdev/code-interpreter:latest\\n...",
       "files": [
          {{
             "filename": "main.py",
             "content": "#path/to/main.py\\n ..."
          }},
          {{
             "filename": "pyproject.toml",
             "content": "#path/to/pyproject.toml\\n ..."
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

    # Convert FileItem objects to list of dicts if needed
    # or just store them directly as data.files is already a list of FileItem
    # GenerateCodeOutput expects a dict, but we can store as a list of dicts
    # by converting each FileItem object:
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
async def run_code_in_e2b(input: RunCodeInput) -> RunCodeOutput:
    log.info("run_code_in_e2b started", input=input)
    template_id = os.environ.get("E2B_TEMPLATE_ID")
    sbx = Sandbox(template_id)

    # Write the files into the sandbox
    for file_item in input.files:
        # Create directory if needed, here we assume /app
        # sbx.commands.run("mkdir -p /app")
        sbx.files.write(f"./{file_item['filename']}", file_item['content'].encode("utf-8"))

    # Run the main Python file (assuming there's a main.py)
    # If there's no main.py, adapt accordingly
    run = sbx.commands.run("python3 ./main.py")

    if run.exit_code != 0:
        return RunCodeOutput(output=run.stderr or run.stdout)
    return RunCodeOutput(output=run.stdout)






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

    # Convert files to a JSON string for the prompt
    # They are in the format [{"filename":"...", "content":"..."}]
    files_str = json.dumps(input.files, indent=2)

    validation_prompt = f"""
    The test conditions: {input.test_conditions}

    dockerfile:
    {input.dockerfile}

    files:
    {files_str}

    output:
    {input.output}

    If all test conditions are met, return:
    {{ "result": true, "dockerfile": null, "files": null }}

    Otherwise, return:
    {{
      "result": false,
      "dockerfile": "FROM e2bdev/code-interpreter:latest\\n...",
      "files": [
        {{
          "filename": "filename.ext",
          "content": "#path/to/filename.ext\\n..."
        }}
      ]
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
        return ValidateOutputOutput(result=False)

    data = result.parsed

    if data.files is not None:
        # Convert FileItem objects to dict format if needed
        updated_files = [{"filename": f.filename, "content": f.content} for f in data.files]
    else:
        updated_files = None

    return ValidateOutputOutput(result=data.result, dockerfile=data.dockerfile, files=updated_files)
