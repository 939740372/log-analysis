import unittest
from pathlib import Path

from log_analysis.config import AnalysisConfig
from log_analysis.react_tools import ReactToolRegistry


class ReactToolTests(unittest.TestCase):
    def test_search_logs_finds_matches(self) -> None:
        tmp_dir = Path("test-output-react")
        tmp_dir.mkdir(exist_ok=True)
        log_file = tmp_dir / "app.log"
        log_file.write_text("hello\nasync method failed\nworld\n", encoding="utf-8")
        try:
            registry = ReactToolRegistry([log_file], None, AnalysisConfig(output_dir=tmp_dir))
            result = registry.search_logs("async method", limit=5)
            self.assertEqual(len(result["matches"]), 1)
            self.assertIn("async method failed", result["matches"][0]["excerpt"])
        finally:
            if log_file.exists():
                log_file.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()


if __name__ == "__main__":
    unittest.main()
