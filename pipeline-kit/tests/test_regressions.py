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
APPLY_PATCH = KIT_ROOT / "apply-patch.sh"


def write_file(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def write_executable(path: pathlib.Path, text: str):
    write_file(path, text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def run_cmd(args, cwd: pathlib.Path):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True)


def run_checked(args, cwd: pathlib.Path):
    result = run_cmd(args, cwd)
    if result.returncode != 0:
        raise AssertionError(
            f"command failed: {args}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


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

    def test_rw_role_skips_text_only_proxy_adapter(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            proxy = root / "proxyapi.sh"
            fallback = root / "fallback.sh"
            write_executable(
                proxy,
                "#!/usr/bin/env bash\n"
                "printf 'proxy should not run\\n'\n",
            )
            write_executable(
                fallback,
                "#!/usr/bin/env bash\n"
                "printf 'fallback ran\\n'\n",
            )
            write_file(root / "task.md", "implement this\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    f"""
                    project: test
                    language: test
                    engines:
                      proxy: {{ adapter: {proxy} }}
                      fallback: {{ adapter: {fallback} }}
                    roles:
                      coder:
                        engine: proxy
                        fallback: [fallback]
                        mode: rw
                    policy:
                      cross_check: false
                      retry: 1
                      timeout_secs: 5
                    """
                ).strip()
                + "\n",
            )

            result = run_cmd([sys.executable, str(DISPATCHER), "coder", "task.md"], root)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "fallback ran\n")
            self.assertIn("skip proxy: mode 'rw' not supported", result.stderr)
            self.assertFalse(list((root / "pipeline" / "logs").glob("*-coder-proxy-1.log")))

    def test_state_as_records_patch_coder_as_coder_for_cross_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            adapter = root / "patcher.sh"
            write_executable(adapter, "#!/usr/bin/env bash\nprintf 'diff --git a/x b/x\\n'\n")
            write_file(root / "task.md", "produce a patch\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    f"""
                    project: test
                    language: test
                    engines:
                      patcher:
                        adapter: {adapter}
                        family: gpt
                    roles:
                      patch-coder:
                        engine: patcher
                        fallback: []
                        mode: ro
                        state_as: coder
                    policy:
                      cross_check: true
                      retry: 1
                      timeout_secs: 5
                    """
                ).strip()
                + "\n",
            )

            result = run_cmd([sys.executable, str(DISPATCHER), "patch-coder", "task.md"], root)

            self.assertEqual(result.returncode, 0, result.stderr)
            state = (root / "pipeline" / "state" / "engines.json").read_text()
            self.assertIn('"coder": "patcher"', state)
            self.assertNotIn('"patch-coder"', state)

    def test_apply_patch_applies_patch_and_runs_project_verification(self):
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            run_checked(["git", "init", "-q"], root)
            write_file(root / "hello.txt", "old\n")
            write_file(
                root / "pipeline.yaml",
                textwrap.dedent(
                    """
                    project: test
                    language: test
                    test_cmd: "grep -q new hello.txt"
                    lint_cmd: "true"
                    engines: {}
                    roles: {}
                    policy: {}
                    """
                ).strip()
                + "\n",
            )
            run_checked(["git", "add", "hello.txt", "pipeline.yaml"], root)
            run_checked(
                [
                    "git",
                    "-c",
                    "user.name=Pipeline Test",
                    "-c",
                    "user.email=pipeline@example.test",
                    "commit",
                    "-q",
                    "-m",
                    "init",
                ],
                root,
            )
            write_file(
                root / "change.patch",
                textwrap.dedent(
                    """
                    diff --git a/hello.txt b/hello.txt
                    --- a/hello.txt
                    +++ b/hello.txt
                    @@ -1 +1 @@
                    -old
                    +new
                    """
                ).lstrip(),
            )

            result = run_cmd([str(APPLY_PATCH), "change.patch"], root)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / "hello.txt").read_text(), "new\n")
            self.assertIn("## Status: done", result.stdout)
            self.assertIn("$ grep -q new hello.txt", result.stdout)

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
