"""
test_code_syntax.py - Fast AST syntax and compilation validation.
Ensures all Python files in functions/ and scripts/ compile cleanly.
"""

from pathlib import Path
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent

PYTHON_FILES = [
    str(p.relative_to(REPO_ROOT))
    for p in (list((REPO_ROOT / "functions").rglob("*.py")) + list((REPO_ROOT / "scripts").glob("*.py")))
    if ".venv" not in p.parts and ".python_packages" not in p.parts and "__pycache__" not in p.parts
]


class TestCodeSyntax(unittest.TestCase):
    def test_all_python_files_compile(self):
        self.assertGreater(len(PYTHON_FILES), 0, "No python files found to test")
        failed = []
        for rel_path in PYTHON_FILES:
            full_path = REPO_ROOT / rel_path
            code = full_path.read_text(encoding="utf-8")
            try:
                compile(code, rel_path, "exec")
            except Exception as e:
                failed.append(f"{rel_path}: {e}")

        self.assertEqual(len(failed), 0, f"Syntax errors in files:\n" + "\n".join(failed))


if __name__ == "__main__":
    unittest.main()
