# src/workflows/workflow.py
from restack_ai.workflow import workflow, import_functions, log
from dataclasses import dataclass
from datetime import timedelta
import csv
import os
from datetime import datetime

with import_functions():
    from src.functions.functions import generate_code, run_code_in_e2b, validate_output
    from src.functions.functions import GenerateCodeInput, RunCodeInput, ValidateOutputInput

@dataclass
class WorkflowInputParams:
    user_prompt: str
    test_conditions: str

@workflow.defn()
class AutonomousCodingWorkflow:
    @workflow.run
    async def run(self, input: WorkflowInputParams):
        log.info("AutonomousCodingWorkflow started", input=input)

        gen_output = await workflow.step(
            generate_code,
            GenerateCodeInput(
                user_prompt=input.user_prompt,
                test_conditions=input.test_conditions
            ),
            start_to_close_timeout=timedelta(seconds=300)
        )

        dockerfile = gen_output.dockerfile
        files = gen_output.files

        log_file = "iterations_log.csv"
        file_exists = os.path.isfile(log_file)
        if not file_exists:
            with open(log_file, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["iteration", "filename", "timestamp"])

        iteration_count = 0
        max_iterations = 20

        while iteration_count < max_iterations:
            iteration_count += 1
            log.info(f"Iteration {iteration_count} start")

            run_output = await workflow.step(
                run_code_in_e2b,
                RunCodeInput(dockerfile=dockerfile, files=files),
                start_to_close_timeout=timedelta(seconds=300)
            )

            val_output = await workflow.step(
                validate_output,
                ValidateOutputInput(
                    dockerfile=dockerfile,
                    files=files,
                    output=run_output.output,
                    test_conditions=input.test_conditions
                ),
                start_to_close_timeout=timedelta(seconds=300)
            )

            if val_output.result:
                log.info("AutonomousCodingWorkflow completed successfully")
                return True
            else:
                changed_files = val_output.files if val_output.files else {}
                if val_output.dockerfile:
                    dockerfile = val_output.dockerfile

                for filename, new_content in changed_files.items():
                    files[filename] = new_content

                if changed_files:
                    now = datetime.utcnow().isoformat()
                    with open(log_file, "a", newline="", encoding="utf-8") as f:
                        writer = csv.writer(f)
                        for changed_filename in changed_files.keys():
                            writer.writerow([iteration_count, changed_filename, now])

        log.warn("AutonomousCodingWorkflow reached max iterations without success")
        return False
