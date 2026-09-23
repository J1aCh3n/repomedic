from pathlib import Path
import os
import subprocess
import unittest

from repomedic.changes import (
    SafetyError, ScanLimits, changed_paths, copy_repository, diff_hash,
    build_diff, normalize_path, scan_tree,
)
from tests.helpers import temporary_directory


class ChangesTests(unittest.TestCase):
    def test_copy_excludes_secrets_git_and_evaluator(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            for name in (".git", "evaluator"):
                (source / name).mkdir()
                (source / name / "private.txt").write_text("private")
            (source / ".env.local").write_text("secret")
            (source / "app.py").write_text("value = 1\n")
            target = root / "copy"
            copy_repository(source, target)
            self.assertEqual(set(scan_tree(target)), {"app.py"})
            self.assertEqual((source / ".env.local").read_text(), "secret")

    def test_paths_reject_cross_platform_escapes(self) -> None:
        for path in ("../outside", "/tmp/file", "C:\\secret", "a/../b", "a\x00b"):
            with self.subTest(path=path), self.assertRaises(SafetyError):
                normalize_path(path)

    def test_changed_paths_and_hash_cover_add_modify_delete(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            before = root / "before"
            before.mkdir()
            (before / "app.py").write_text("value = 1\n")
            (before / "delete.py").write_text("old\n")
            after = root / "after"
            copy_repository(before, after)
            (after / "app.py").write_text("value = 2\n")
            (after / "delete.py").unlink()
            (after / "new.py").write_text("new")
            self.assertEqual(changed_paths(scan_tree(before), scan_tree(after)),
                             ["app.py", "delete.py", "new.py"])
            diff = build_diff(before, after)
            self.assertIn("\\ No newline at end of file", diff)
            self.assertNotEqual(diff_hash(diff), diff_hash(diff + "changed"))

    def test_file_and_entry_limits_fail_closed(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            (root / "big.py").write_text("12345")
            with self.assertRaisesRegex(SafetyError, "size"):
                scan_tree(root, ScanLimits(max_file_bytes=4))
            (root / "other.py").write_text("")
            with self.assertRaisesRegex(SafetyError, "entries"):
                scan_tree(root, ScanLimits(max_entries=1))

    def test_exported_diff_roundtrips_crlf_empty_files_and_missing_newline(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            before = root / "before"
            before.mkdir()
            (before / "app.py").write_bytes(b"value = 1\r\n")
            (before / "empty.py").write_bytes(b"")
            after = root / "after"
            copy_repository(before, after)
            (after / "app.py").write_bytes(b"value = 2\r\n")
            (after / "empty.py").unlink()
            (after / "added.py").write_bytes(b"")
            (after / "tail.py").write_bytes(b"no final newline")
            # Git counts LF lines; Unicode separators and standalone CR are data.
            (before / "separators.txt").write_bytes("old\u2028line\rvalue\n".encode())
            (after / "separators.txt").write_bytes("new\u2028line\rvalue\n".encode())
            replica = root / "replica"
            copy_repository(before, replica)
            patch = root / "patch.diff"
            diff = build_diff(before, after)
            self.assertIn("\r\n", diff)
            patch.write_bytes(diff.encode("utf-8"))
            environment = {key: value for key, value in os.environ.items()
                           if key in {"PATH", "SYSTEMROOT", "TEMP", "TMP"}}
            environment["GIT_CEILING_DIRECTORIES"] = str(replica.parent.resolve())
            result = subprocess.run(["git", "apply", str(patch.resolve())], cwd=replica,
                                    env=environment, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(scan_tree(replica), scan_tree(after))

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_fifo_is_rejected_without_opening_it(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            os.mkfifo(root / "pipe")
            with self.assertRaisesRegex(SafetyError, "ordinary"):
                scan_tree(root)

    def test_symlink_is_rejected_when_supported(self) -> None:
        with temporary_directory() as directory:
            root = Path(directory)
            (root / "real.py").write_text("real")
            try:
                (root / "link.py").symlink_to(root / "real.py")
            except OSError:
                self.skipTest("symlinks unavailable")
            with self.assertRaises(SafetyError):
                scan_tree(root)
