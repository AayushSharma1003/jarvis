"""The build script's classification of collected package files.

Why this exists: `espeakng_loader.get_library_path()` builds
`Path(__file__).parent / "libespeak-ng.<ext>"` at RUNTIME, so the library has
to land beside the package in the frozen tree or the lookup misses it and the
build ships mute -- gotcha 30, whose fix (`collect_data_files`) turned out to
work on macOS and Windows and never once on Linux.

The cause is a single asymmetry: `collect_data_files` excludes everything
ending in `PyInstaller.compat.ALL_SUFFIXES`, which is Python's *extension
module* suffix list -- `['.py', '.pyc', '.cpython-313-darwin.so', '.abi3.so',
'.so']`. `.dylib` and `.dll` are not in it and are collected as data; a Linux
shared library IS `.so`, so it is dropped **silently**. The release workflow's
Linux job failed on exactly that, on the first tag anyone ever pushed.

So libraries are collected explicitly from the installed package directory and
stripped back out of the `collect_data_files` result on the platforms where it
does return them.

These are tested rather than the spec because a .spec file is not importable;
the spec is a thin caller of `collect_package_libraries` + `drop_libraries`.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from build_sidecar import (  # noqa: E402
    collect_package_libraries,
    drop_libraries,
    is_shared_library,
)


class TestIsSharedLibrary:
    @pytest.mark.parametrize(
        "name",
        [
            "libespeak-ng.so",  # Linux, the one that broke the release
            "libespeak-ng.so.1",  # Linux soname
            "libespeak-ng.so.1.52.0",  # Linux fully-versioned
            "libespeak-ng.dylib",  # macOS, what get_library_path() returns
            "libespeak-ng.1.dylib",  # macOS versioned
            "libespeak-ng.1.52.0.dylib",
            "espeak-ng.dll",  # Windows
        ],
    )
    def test_library_names_are_libraries(self, name):
        assert is_shared_library(name)

    @pytest.mark.parametrize(
        "name",
        [
            "en_dict",  # espeak-ng-data, 366 files of this shape
            "config.json",
            "phontab",
            "notes.sox",  # a bare substring test would call this a library
            "af_dict",
        ],
    )
    def test_data_names_are_not_libraries(self, name):
        assert not is_shared_library(name)


class TestDropLibraries:
    def test_macos_dylibs_are_removed_so_they_are_not_declared_twice(self):
        """collect_data_files DOES return .dylib/.dll (they are not in
        ALL_SUFFIXES). collect_package_libraries returns them too, so without
        this they would be declared in both datas and binaries."""
        kept = drop_libraries(
            [
                ("/x/libespeak-ng.dylib", "espeakng_loader"),
                ("/x/libespeak-ng.1.52.0.dylib", "espeakng_loader"),
                ("/x/espeak-ng-data/phontab", "espeakng_loader/espeak-ng-data"),
            ]
        )
        assert kept == [("/x/espeak-ng-data/phontab", "espeakng_loader/espeak-ng-data")]

    def test_data_files_are_untouched(self):
        collected = [
            ("/x/config.json", "kokoro_onnx"),
            ("/x/espeak-ng-data/en_dict", "espeakng_loader/espeak-ng-data"),
        ]
        assert drop_libraries(collected) == collected


class TestCollectPackageLibraries:
    def test_finds_the_linux_library_collect_data_files_drops(self, tmp_path):
        """The regression. On Linux `.so` is in ALL_SUFFIXES, so
        collect_data_files silently omits libespeak-ng.so and the build ships
        mute -- which is what failed the first release tag ever pushed."""
        pkg = tmp_path / "espeakng_loader"
        (pkg / "espeak-ng-data").mkdir(parents=True)
        (pkg / "libespeak-ng.so").write_bytes(b"\x7fELF")
        (pkg / "__init__.py").write_text("")
        (pkg / "espeak-ng-data" / "en_dict").write_bytes(b"data")

        found = collect_package_libraries(pkg, "espeakng_loader")

        assert found == [(str(pkg / "libespeak-ng.so"), "espeakng_loader")]

    def test_versioned_copies_are_all_collected(self, tmp_path):
        """The gate compares against every non-.py file in the venv, so missing
        a versioned copy fails the build just as a missing soname would."""
        pkg = tmp_path / "espeakng_loader"
        pkg.mkdir()
        for name in ("libespeak-ng.dylib", "libespeak-ng.1.dylib", "libespeak-ng.1.52.0.dylib"):
            (pkg / name).write_bytes(b"\xcf\xfa\xed\xfe")

        found = collect_package_libraries(pkg, "espeakng_loader")

        assert len(found) == 3
        assert {Path(s).name for s, _ in found} == {
            "libespeak-ng.dylib",
            "libespeak-ng.1.dylib",
            "libespeak-ng.1.52.0.dylib",
        }

    def test_destination_stays_package_relative_in_subdirectories(self, tmp_path):
        """get_library_path() resolves Path(__file__).parent / name, so a
        library flattened to the bundle root is invisible to it."""
        pkg = tmp_path / "espeakng_loader"
        (pkg / "nested").mkdir(parents=True)
        (pkg / "nested" / "libhelper.so").write_bytes(b"\x7fELF")

        found = collect_package_libraries(pkg, "espeakng_loader")

        assert found == [(str(pkg / "nested" / "libhelper.so"), "espeakng_loader/nested")]

    def test_data_files_are_not_collected_as_libraries(self, tmp_path):
        pkg = tmp_path / "espeakng_loader"
        (pkg / "espeak-ng-data").mkdir(parents=True)
        (pkg / "espeak-ng-data" / "en_dict").write_bytes(b"data")
        (pkg / "__init__.py").write_text("")

        assert collect_package_libraries(pkg, "espeakng_loader") == []
