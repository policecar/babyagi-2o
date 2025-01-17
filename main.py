import os
import json
import traceback
import subprocess
import yaml
from time import sleep
from litellm import completion
from typing import Optional, Dict


# ANSI escape codes for color and formatting
class Colors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


# Configuration
MODEL_NAME = os.environ.get("LITELLM_MODEL", "anthropic/claude-3-5-sonnet-20240620")
tools, available_functions = [], {}
MAX_TOOL_OUTPUT_LENGTH = 5000  # Adjust as needed

# Automatically detect available API keys
api_key_patterns = ["API_KEY", "ACCESS_TOKEN", "SECRET_KEY", "TOKEN", "APISECRET"]
available_api_keys = [
    key
    for key in os.environ.keys()
    if any(pattern in key.upper() for pattern in api_key_patterns)
]


def get_prompt_from_promptlib(prompt_lib: str) -> Dict[str, str]:
    """Load prompts from the YAML prompt library with interactive selection."""
    try:
        with open(prompt_lib, "r", encoding="utf-8") as f:
            all_prompts = yaml.safe_load(f)
            print(f"\n{Colors.OKBLUE}Available prompts:{Colors.ENDC}")
            for idx, key in enumerate(all_prompts.keys(), 1):
                print(f"{idx}. {key}")
            while True:
                try:
                    choice = (
                        int(
                            input(f"\n{Colors.BOLD}Select prompt number: {Colors.ENDC}")
                        )
                        - 1
                    )
                    if 0 <= choice < len(all_prompts):
                        selected_key = list(all_prompts.keys())[choice]
                        return all_prompts[selected_key]
                    else:
                        print(
                            f"{Colors.FAIL}Invalid choice. Please select a valid prompt number.{Colors.ENDC}"
                        )
                except (ValueError, IndexError):
                    print(
                        f"{Colors.FAIL}Invalid input. Please enter a number corresponding to the prompt.{Colors.ENDC}"
                    )
    except Exception as e:
        print(f"{Colors.FAIL}Error loading prompts: {e}{Colors.ENDC}")
        return {"system": "", "user": ""}


def read_user_input(input_file: Optional[str] = None) -> str:
    """Read user input from a file or stdin."""
    if input_file:
        try:
            with open(input_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception as e:
            print(f"{Colors.FAIL}Error reading input file: {e}{Colors.ENDC}")
            return ""
    return input(f"{Colors.BOLD}Describe the task you want to complete: {Colors.ENDC}")


def register_tool(name, func, description, parameters):
    global tools
    tools = [tool for tool in tools if tool["function"]["name"] != name]
    available_functions[name] = func
    tools.append(
        {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": parameters,
                    "required": list(parameters.keys()),
                },
            },
        }
    )
    print(f"{Colors.OKGREEN}{Colors.BOLD}Registered tool:{Colors.ENDC} {name}")


def create_or_update_tool(name, code, description, parameters):
    try:
        exec(code, globals())
        register_tool(name, globals()[name], description, parameters)
        return f"Tool '{name}' created/updated successfully."
    except Exception as e:
        return f"Error creating/updating tool '{name}': {e}"


def install_package(package_name):
    try:
        subprocess.check_call(["uv", "pip", "install", package_name])
        return f"Package '{package_name}' installed successfully."
    except Exception as e:
        return f"Error installing package '{package_name}': {e}"


def serialize_tool_result(tool_result, max_length=MAX_TOOL_OUTPUT_LENGTH):
    try:
        serialized_result = json.dumps(tool_result)
    except TypeError:
        serialized_result = str(tool_result)
    if len(serialized_result) > max_length:
        return (
            serialized_result[:max_length]
            + f"\n\n{Colors.WARNING}(Note: Result was truncated to {max_length} characters out of {len(serialized_result)} total characters.){Colors.ENDC}"
        )
    else:
        return serialized_result


def call_tool(function_name, args):
    func = available_functions.get(function_name)
    if not func:
        print(
            f"{Colors.FAIL}{Colors.BOLD}Error:{Colors.ENDC} Tool '{function_name}' not found."
        )
        return f"Tool '{function_name}' not found."
    try:
        print(
            f"{Colors.OKBLUE}{Colors.BOLD}Calling tool:{Colors.ENDC} {function_name} with args: {args}"
        )
        result = func(**args)
        print(
            f"{Colors.OKCYAN}{Colors.BOLD}Result of {function_name}:{Colors.ENDC} {result}"
        )
        return result
    except Exception as e:
        print(
            f"{Colors.FAIL}{Colors.BOLD}Error:{Colors.ENDC} Error executing '{function_name}': {e}"
        )
        return f"Error executing '{function_name}': {e}"


def task_completed():
    return "Task marked as completed."


# Initialize basic tools
register_tool(
    "create_or_update_tool",
    create_or_update_tool,
    "Creates or updates a tool with the specified name, code, description, and parameters.",
    {
        "name": {"type": "string", "description": "The tool name."},
        "code": {"type": "string", "description": "The Python code for the tool."},
        "description": {"type": "string", "description": "A description of the tool."},
        "parameters": {
            "type": "object",
            "description": "A dictionary defining the parameters for the tool.",
            "additionalProperties": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "description": "Data type of the parameter.",
                    },
                    "description": {
                        "type": "string",
                        "description": "Description of the parameter.",
                    },
                },
                "required": ["type", "description"],
            },
        },
    },
)

register_tool(
    "install_package",
    install_package,
    "Installs a Python package using pip.",
    {
        "package_name": {
            "type": "string",
            "description": "The name of the package to install.",
        }
    },
)

register_tool(
    "task_completed", task_completed, "Marks the current task as completed.", {}
)


def run_main_loop(prompts: Dict[str, str]):
    """Run the main LM interaction loop."""

    # Include available API keys in the system prompt
    api_keys_info = (
        "Available API keys:\n"
        + "\n".join(f"- {key}" for key in available_api_keys)
        + "\n\n"
        if available_api_keys
        else "No API keys are available.\n\n"
    )

    # Combine default system prompt with custom prompt from library
    system_prompt = prompts.get("system", "").format(api_keys_info=api_keys_info)

    # Initialize messages with system prompt and user input
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompts.get("user")},
    ]

    iteration, max_iterations = 0, 50
    while iteration < max_iterations:
        print(
            f"{Colors.HEADER}{Colors.BOLD}Iteration {iteration + 1} running...{Colors.ENDC}"
        )
        try:
            response = completion(
                model=MODEL_NAME, messages=messages, tools=tools, tool_choice="auto"
            )
            response_message = response.choices[0].message
            if response_message.content:
                print(
                    f"{Colors.OKCYAN}{Colors.BOLD}LLM Response:{Colors.ENDC}\n{response_message.content}\n"
                )
            messages.append(response_message)
            if response_message.tool_calls:
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    tool_result = call_tool(function_name, args)
                    serialized_tool_result = serialize_tool_result(tool_result)
                    messages.append(
                        {
                            "role": "tool",
                            "name": function_name,
                            "tool_call_id": tool_call.id,
                            "content": serialized_tool_result,
                        }
                    )
                if "task_completed" in [
                    tc.function.name for tc in response_message.tool_calls
                ]:
                    print(f"{Colors.OKGREEN}{Colors.BOLD}Task completed.{Colors.ENDC}")
                    break
        except Exception as e:
            print(
                f"{Colors.FAIL}{Colors.BOLD}Error:{Colors.ENDC} Error in main loop: {e}"
            )
            traceback.print_exc()
        iteration += 1
        sleep(2)
    print(
        f"{Colors.WARNING}{Colors.BOLD}Max iterations reached or task completed.{Colors.ENDC}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Run the AI assistant with optional file input and prompt library"
    )
    parser.add_argument(
        "-i", "--input", help="Path to input file containing user task", type=str
    )
    parser.add_argument("-p", "--prompts", help="Path to YAML prompt library", type=str)

    args = parser.parse_args()

    # Try to install yaml if not present
    try:
        import yaml
    except ImportError:
        print(f"{Colors.WARNING}Installing PyYAML...{Colors.ENDC}")
        subprocess.check_call(["uv", "pip", "install", "pyyaml"])
        import yaml

    # Load prompt
    if args.prompts:
        prompts = get_prompt_from_promptlib(args.prompts)
    else:
        with open("prompts.yaml", "r", encoding="utf-8") as f:
            prompts = yaml.safe_load(f)["default"]

    prompts["user"] = read_user_input(args.input)
    run_main_loop(prompts)
