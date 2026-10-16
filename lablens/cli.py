import argparse
import json
import os
import tempfile
from pathlib import Path
from .agent import Agent, DEMO_QUESTION
from .model import HTTPModel, ReplayModel, ModelError
from .tools import ToolBox
from .datasets import DatasetBundle
from .schemas import QueryRuns
from .workbench import QueryRequest, inspect, preview, execute, verify
from .reporting import export_report


def main():
    parser = argparse.ArgumentParser(description="LabLens experiment analysis agent")
    parser.add_argument("command", choices=["demo", "ask", "inspect", "query", "verify", "report"])
    parser.add_argument("question", nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--query-file", type=Path)
    parser.add_argument("--format", choices=["html", "markdown"], default="html")
    args = parser.parse_args()
    if args.command == "ask" and not args.question:
        parser.error("ask requires a question")
    if args.command == "demo" and args.data_dir:
        parser.error("demo uses the bundled fixed scenario; use query or ask for custom data")
    if args.command == 'report':
        if not args.question or not args.output:
            parser.error('report requires a receipt JSON path and --output')
        if args.data_dir or args.query_file:
            parser.error('report uses only the enclosed receipt; --data-dir and --query-file are not supported')
        try:
            source = Path(args.question)
            if source.resolve() == args.output.resolve() or (source.exists() and args.output.exists() and source.samefile(args.output)):
                raise ValueError('report output must differ from the input receipt path')
            result = export_report(json.loads(source.read_text()), args.format)
            # Complete validation and rendering before touching the destination.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as stream:
                    temp_path = Path(stream.name)
                    stream.write(result['content'])
                os.replace(temp_path, args.output)
            finally:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()
        except (ValueError, TypeError, OSError) as exc:
            parser.exit(2, str(exc) + "\n")
        print(f"Report saved: {args.output} ({result['report_version']}; export model calls: 0)")
        return
    try:
        dataset = None
        if args.data_dir:
            t=ToolBox(data_dir=args.data_dir)
            dataset=DatasetBundle.model_validate({'runs':t.runs,'notes':t.notes})
        if args.command == 'inspect':
            result=inspect(dataset)
        elif args.command == 'query':
            if not args.query_file:parser.error('query requires --query-file')
            request=QueryRequest(query=QueryRuns.model_validate_json(args.query_file.read_text()),dataset=dataset)
            request.preview_id=preview(request)['preview_id']
            result=execute(request)['receipt']
        elif args.command == 'verify':
            if not args.question:parser.error('verify requires a receipt JSON path')
            result=verify(json.loads(Path(args.question).read_text()))
        else:
            tools=ToolBox(bundle=dataset.model_dump()) if dataset else ToolBox()
            result=Agent(ReplayModel() if args.command == "demo" else HTTPModel(),tools=tools).run(DEMO_QUESTION if args.command == "demo" else args.question)
    except (ModelError, ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    if args.command in {"ask", "demo"} and result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
