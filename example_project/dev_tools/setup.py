from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPy(build_py):
    def find_package_modules(self, package, package_dir):
        # The source directory is also the package root; setup.py is build metadata.
        return [
            module
            for module in super().find_package_modules(package, package_dir)
            if module[1] != "setup"
        ]


setup(
    name="scm_dev_tools",
    version="0.5.0",
    description="Bamboozle reversible text conversion and verification CLI",
    author="deadcoast",
    packages=["bamboozle"],
    package_dir={"bamboozle": "."},
    cmdclass={"build_py": BuildPy},
    package_data={"bamboozle": ["cipher_dict.json", "*.ps1", "*.bat", "*.md"]},
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "bamboozle=bamboozle.cli:main",
            "scm-cipher=bamboozle.cipher:main",
        ],
    },
    python_requires=">=3.8",
    install_requires=[],
)
