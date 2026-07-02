from __future__ import annotations

import argparse
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .json_view import render_json_document
from .markdown_view import render_markdown_document
from .output_paths import ensure_output_root, resolve_output_dir
from .publish import refresh_output_index


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动日志分析报告的静态 Web 服务。")
    parser.add_argument("--output-root", default="output", help="报告根目录，默认使用 output。")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址。")
    parser.add_argument("--port", type=int, default=8765, help="监听端口。")
    return parser


class ReportRequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        local_path = self.translate_path(self.path)
        path = Path(local_path)
        if path.is_file() and path.suffix.lower() == ".md":
            self._serve_markdown(path)
            return
        if path.is_file() and path.suffix.lower() == ".json":
            self._serve_json(path)
            return
        super().do_GET()

    def do_HEAD(self) -> None:  # noqa: N802
        local_path = self.translate_path(self.path)
        path = Path(local_path)
        if path.is_file() and path.suffix.lower() == ".md":
            body = render_markdown_document(
                path.name,
                path.read_text(encoding="utf-8", errors="replace"),
                home_href=self._report_home_href(path),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        if path.is_file() and path.suffix.lower() == ".json":
            body = render_json_document(
                path.name,
                json.loads(path.read_text(encoding="utf-8", errors="replace")),
                home_href=self._report_home_href(path),
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return
        super().do_HEAD()

    def guess_type(self, path: str) -> str:
        guessed = super().guess_type(path)
        lower = path.lower()
        if lower.endswith(".html"):
            return "text/html; charset=utf-8"
        if lower.endswith(".css"):
            return "text/css; charset=utf-8"
        if lower.endswith(".js"):
            return "application/javascript; charset=utf-8"
        return guessed

    def _serve_markdown(self, path: Path) -> None:
        markdown_text = path.read_text(encoding="utf-8", errors="replace")
        document = render_markdown_document(path.name, markdown_text, home_href=self._report_home_href(path)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(document)))
        self.end_headers()
        self.wfile.write(document)

    def _serve_json(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        body = render_json_document(path.name, payload, home_href=self._report_home_href(path)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _report_home_href(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(Path(self.directory).resolve())
        except ValueError:
            return "/"
        if not relative.parts:
            return "/"
        report_root = relative.parts[0]
        return f"/{report_root}/site/index.html"


def main() -> None:
    args = build_parser().parse_args()
    if args.output_root == "output":
        output_root = ensure_output_root()
    else:
        output_root = resolve_output_dir(args.output_root)
        output_root.mkdir(parents=True, exist_ok=True)
    refresh_output_index(Path(output_root))
    handler = partial(ReportRequestHandler, directory=str(output_root))
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
