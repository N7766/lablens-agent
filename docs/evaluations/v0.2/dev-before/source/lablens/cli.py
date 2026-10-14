import argparse
import json
from pathlib import Path
from .agent import Agent, DEMO_QUESTION
from .model import HTTPModel, ReplayModel, ModelError


def main():
    parser = argparse.ArgumentParser(description="LabLens experiment analysis agent")
    parser.add_argument("command", choices=["demo", "ask"])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "ask" and not args.question:
        parser.error("ask requires a question")
    try:
        result = Agent(ReplayModel() if args.command == "demo" else HTTPModel()).run(DEMO_QUESTION if args.command == "demo" else args.question)
    except (ModelError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
