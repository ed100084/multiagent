import pathlib
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest


KIT_ROOT = pathlib.Path(__file__).resolve().parents[1]
DISPATCHER = KIT_ROOT / "dispatcher.py"
COUNCIL = KIT_ROOT / "council.py"


def write_file(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_executable(path: pathlib.Path, text: str):
    write_file(path, text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_cmd(args, cwd: pathlib.Path):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


class PipelineRegressionTests(unittest.TestCase):
    def test_reviewer_noncompliant_output_fails_over_without_stdout_pollution(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            bad = root / "bad.sh"
            good = root / "good.sh"
            write_executable(bad, "#!/usr/bin/env bash\nprintf '# Review Report\\nno verdict\\n'\n")
            write_executable(
                good,
                "#!/usr/bin/env bash\n"
                "printf '# Review Report\\nverdict: pass\\n## Findings\\nno findings\\n'\n",
            )
            write_file(root / "task.md", "review this\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    f"""
                    project: test
                    language: test
                    engines:
                      bad: {{ adapter: {bad} }}
                      good: {{ adapter: {good} }}
                    roles:
                      reviewer:
                        engine: bad
                        fallback: [good]
                        mode: ro
                    policy:
                      cross_check: false
                      retry: 1
                      timeout_secs: 5
                    """
                ).strip()
                + "\n",
            )

            result = run_cmd([sys.executable, str(DISPATCHER), "reviewer", "task.md"], root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("verdict: pass", result.stdout)
            self.assertNotIn("no verdict", result.stdout)

            logs = list((root / "pipeline" / "logs").glob("*-reviewer-bad-1.log"))
            self.assertEqual(len(logs), 1)
            self.assertIn("no verdict", logs[0].read_text())

    def test_reviewer_verdict_fail_is_preserved_and_does_not_fail_over(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            fail = root / "fail.sh"
            fallback = root / "fallback.sh"
            write_executable(
                fail,
                "#!/usr/bin/env bash\n"
                "printf '# Review Report\\nverdict: fail\\n## Findings\\n- high issue\\n'\n",
            )
            write_executable(
                fallback,
                "#!/usr/bin/env bash\n"
                "printf '# Review Report\\nverdict: pass\\n## Findings\\nshould not run\\n'\n",
            )
            write_file(root / "task.md", "review this\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    f"""
                    project: test
                    language: test
                    engines:
                      fail: {{ adapter: {fail} }}
                      fallback: {{ adapter: {fallback} }}
                    roles:
                      reviewer:
                        engine: fail
                        fallback: [fallback]
                        mode: ro
                    policy:
                      cross_check: false
                      retry: 1
                      timeout_secs: 5
                    """
                ).strip()
                + "\n",
            )

            result = run_cmd([sys.executable, str(DISPATCHER), "reviewer", "task.md"], root)

            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("verdict: fail", result.stdout)
            self.assertIn("- high issue", result.stdout)
            self.assertNotIn("should not run", result.stdout)

    def test_dispatcher_passes_absolute_task_path_to_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            adapter = root / "echo-task-path.sh"
            write_executable(adapter, "#!/usr/bin/env bash\nprintf '%s\\n' \"$2\"\n")
            task = root / "task.md"
            write_file(task, "summarize this\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    f"""
                    project: test
                    language: test
                    engines:
                      echoer: {{ adapter: {adapter} }}
                    roles:
                      summarizer:
                        engine: echoer
                        fallback: []
                        mode: ro
                    policy:
                      cross_check: false
                      retry: 1
                      timeout_secs: 5
                    """
                ).strip()
                + "\n",
            )

            result = run_cmd(
                [sys.executable, str(DISPATCHER), "summarizer", "task.md", "--workdir", "/tmp"],
                root,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(pathlib.Path(result.stdout.strip()), task.resolve())

    def test_council_rejects_unsafe_run_id(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_minimal_council_project(root)

            result = run_cmd([sys.executable, str(COUNCIL), "question.md", "--id", "../x"], root)

            self.assertEqual(result.returncode, 2)
            self.assertIn("invalid council run id", result.stderr)

    def test_council_rejects_existing_output_directory(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            self._write_minimal_council_project(root)
            (root / "pipeline" / "council" / "existing").mkdir(parents=True)

            result = run_cmd([sys.executable, str(COUNCIL), "question.md", "--id", "existing"], root)

            self.assertEqual(result.returncode, 2)
            self.assertIn("council output dir already exists", result.stderr)

    def _write_minimal_council_project(self, root: pathlib.Path):
        adapter = root / "unused.sh"
        write_executable(adapter, "#!/usr/bin/env bash\nprintf '# Answer\\nunused\\n'\n")
        write_file(root / "question.md", "question\n")
        write_file(
            root / "pipeline.yaml",
            textwrap.dedent(
                f"""
                project: test
                language: test
                engines:
                  mock:
                    adapter: {adapter}
                    family: mock
                council:
                  panel: [mock]
                  min_panel: 1
                  rounds: 0
                  judge: mock
                  judge_fallback: []
                policy:
                  cross_check: false
                  retry: 1
                  timeout_secs: 5
                """
            ).strip()
            + "\n",
        )


if __name__ == "__main__":
    unittest.main()
