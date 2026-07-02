from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .output_paths import ensure_output_root, resolve_output_dir
from .publish import refresh_output_index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动日志分析报告的静态 Web 服务。")
    parser.add_argument("--output-root", default="output", help="报告根目录，默认使用 output。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", type=int, default=8765, help="监听端口。")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.output_root == "output":
        output_root = ensure_output_root()
    else:
        output_root = resolve_output_dir(args.output_root)
        output_root.mkdir(parents=True, exist_ok=True)
    refresh_output_index(Path(output_root))
    handler = partial(SimpleHTTPRequestHandler, directory=str(output_root))
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Serving reports at http://{args.host}:{args.port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
