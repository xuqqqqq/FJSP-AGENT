from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from harness_agent.workers.opencode_executable import (
    normalize_opencode_prompt,
    resolve_opencode_executable,
)


class OpenCodeExecutableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.windows = patch("harness_agent.workers.opencode_executable.platform.system", return_value="Windows")
        self.windows.start()
        self.addCleanup(self.windows.stop)
        machine = patch("harness_agent.workers.opencode_executable.platform.machine", return_value="AMD64")
        machine.start()
        self.addCleanup(machine.stop)

    def file(self, relative: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test fixture, never executed")
        return path

    def resolve_from(self, shim: Path) -> str | None:
        with patch("harness_agent.workers.opencode_executable.shutil.which", return_value=str(shim)) as which:
            result = resolve_opencode_executable()
        which.assert_called_once_with("opencode")
        return result

    def test_npm_installed_entry_precedes_platform_packages(self) -> None:
        shim = self.file("npm/opencode.CMD")
        native = self.file("npm/node_modules/opencode-ai/bin/opencode.exe")
        self.file("npm/node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe")
        self.assertEqual(str(native.resolve()), self.resolve_from(shim))

    def test_nested_and_hoisted_platform_packages(self) -> None:
        layouts = (
            "node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe",
            "node_modules/opencode-windows-x64/bin/opencode.exe",
            "node_modules/opencode-ai/node_modules/opencode-windows-x64-baseline/bin/opencode.exe",
        )
        for index, layout in enumerate(layouts):
            with self.subTest(layout=layout):
                shim = self.file(f"npm{index}/opencode.cmd")
                native = self.file(f"npm{index}/{layout}")
                self.assertEqual(str(native.resolve()), self.resolve_from(shim))

    def test_local_node_modules_bin_shim(self) -> None:
        shim = self.file("project/node_modules/.bin/opencode.cmd")
        native = self.file("project/node_modules/opencode-ai/bin/opencode.exe")
        self.assertEqual(str(native.resolve()), self.resolve_from(shim))

    def test_baseline_package_precedes_avx2_package(self) -> None:
        shim = self.file("baseline/opencode.cmd")
        self.file("baseline/node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe")
        native = self.file("baseline/node_modules/opencode-ai/node_modules/opencode-windows-x64-baseline/bin/opencode.exe")
        self.assertEqual(str(native.resolve()), self.resolve_from(shim))

    def test_unknown_architecture_retains_wrapper(self) -> None:
        shim = self.file("unknown/opencode.cmd")
        self.file("unknown/node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe")
        with patch("harness_agent.workers.opencode_executable.platform.machine", return_value="unknown"):
            self.assertEqual(str(shim), self.resolve_from(shim))

    def test_explicit_native_and_wrapper_paths_are_respected(self) -> None:
        for name in ("custom/opencode.exe", "custom/opencode.cmd"):
            with self.subTest(name=name):
                explicit = self.file(name)
                self.file("custom/node_modules/opencode-ai/bin/opencode.exe")
                with patch("harness_agent.workers.opencode_executable.shutil.which") as which:
                    self.assertEqual(str(explicit.resolve()), resolve_opencode_executable(str(explicit)))
                which.assert_not_called()

    def test_linux_does_not_replace_shim(self) -> None:
        shim = self.file("linux/opencode")
        self.file("linux/node_modules/opencode-ai/bin/opencode.exe")
        with patch("harness_agent.workers.opencode_executable.platform.system", return_value="Linux"):
            self.assertEqual(str(shim), self.resolve_from(shim))

    def test_custom_wrapper_without_native_is_returned(self) -> None:
        shim = self.file("custom/opencode.cmd")
        self.assertEqual(str(shim), self.resolve_from(shim))

    def test_custom_command_name_is_not_redirected(self) -> None:
        shim = self.file("custom/opencode.cmd")
        self.file("custom/node_modules/opencode-ai/bin/opencode.exe")
        with patch("harness_agent.workers.opencode_executable.shutil.which", return_value=str(shim)):
            self.assertEqual(str(shim), resolve_opencode_executable("my-opencode"))

    def test_missing_executable_returns_none(self) -> None:
        with patch("harness_agent.workers.opencode_executable.shutil.which", return_value=None):
            self.assertIsNone(resolve_opencode_executable(str(self.root / "missing.exe")))

    def test_native_path_match_is_unchanged(self) -> None:
        native = self.file("native/opencode.exe")
        self.assertEqual(str(native), self.resolve_from(native))

    def test_arm64_does_not_select_x64_package(self) -> None:
        shim = self.file("arm/opencode.cmd")
        self.file("arm/node_modules/opencode-ai/node_modules/opencode-windows-x64/bin/opencode.exe")
        with patch("harness_agent.workers.opencode_executable.platform.machine", return_value="ARM64"):
            self.assertEqual(str(shim), self.resolve_from(shim))
            native = self.file("arm/node_modules/opencode-ai/node_modules/opencode-windows-arm64/bin/opencode.exe")
            self.assertEqual(str(native.resolve()), self.resolve_from(shim))

    def test_directory_is_not_selected_as_native_binary(self) -> None:
        shim = self.file("dirs/opencode.cmd")
        (shim.parent / "node_modules/opencode-ai/bin/opencode.exe").mkdir(parents=True)
        self.assertEqual(str(shim), self.resolve_from(shim))

    def test_batch_prompt_normalizes_cr_lf_and_warns(self) -> None:
        for wrapper in ("opencode.CMD", "custom.bat"):
            with self.subTest(wrapper=wrapper), self.assertWarnsRegex(RuntimeWarning, "attachment argument truncation"):
                self.assertEqual("first second third fourth", normalize_opencode_prompt("first\r\nsecond\nthird\rfourth", wrapper))

    def test_native_and_single_line_prompts_are_unchanged_without_warning(self) -> None:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            for executable in ("opencode.exe", "opencode", None):
                self.assertEqual("a\nb", normalize_opencode_prompt("a\nb", executable))
            self.assertEqual("a  b", normalize_opencode_prompt("a  b", "opencode.cmd"))
        self.assertEqual([], caught)

    def test_linux_prompt_is_unchanged_even_with_cmd_suffix(self) -> None:
        with patch("harness_agent.workers.opencode_executable.platform.system", return_value="Linux"):
            self.assertEqual("a\nb", normalize_opencode_prompt("a\nb", "opencode.cmd"))


if __name__ == "__main__":
    unittest.main()
