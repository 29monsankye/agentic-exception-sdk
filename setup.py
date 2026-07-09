import glob
import os
import sys

sys.path.insert(0, "src")

from Cython.Build import cythonize
from setuptools import Extension, find_packages, setup
from setuptools.command.build_py import build_py as _build_py


# except* / ExceptionGroup syntax is not yet supported by Cython's parser.
_CYTHON_EXCLUDE = {
    "src/agentic_exception_sdk/multi_agent/parallel.py",
}

# Pure-Python package shipped as readable source alongside the compiled SDK.
_EXAMPLES_PKG = "agentic_exception_sdk_examples"


def _extensions(src_dir: str = "src") -> list[Extension]:
    result = []
    for fpath in sorted(glob.glob(f"{src_dir}/agentic_exception_sdk/**/*.py", recursive=True)):
        if fpath in _CYTHON_EXCLUDE:
            continue
        module = os.path.relpath(fpath, src_dir).replace(os.sep, ".")[:-3]
        result.append(Extension(module, [fpath]))
    return result


class _CythonBuildPy(_build_py):
    """Ship .pyi stubs, py.typed, Cython-excluded .py files, and the examples package as plain source."""

    def find_modules(self):
        return []

    def find_package_modules(self, package, package_dir):
        return [
            (pkg, mod, fpath)
            for pkg, mod, fpath in super().find_package_modules(package, package_dir)
            if fpath in _CYTHON_EXCLUDE or package.startswith(_EXAMPLES_PKG)
        ]


_packages = find_packages("src")
_package_data = {pkg: ["*.pyi"] for pkg in _packages}
_package_data["agentic_exception_sdk"].append("py.typed")

setup(
    packages=_packages,
    package_dir={"": "src"},
    package_data=_package_data,
    cmdclass={"build_py": _CythonBuildPy},
    ext_modules=cythonize(
        _extensions(),
        language_level=3,
        compiler_directives={"embedsignature": True},
        nthreads=4,
    ),
)
