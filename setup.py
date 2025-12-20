#!/usr/bin/env python3
"""
MFT Professional System - Setup Script
"""

from setuptools import setup, find_packages
from pathlib import Path

# Read the README file
this_directory = Path(__file__).parent
long_description = ""
readme_path = this_directory / "README.md"
if readme_path.exists():
    long_description = readme_path.read_text(encoding='utf-8')

# Read requirements
requirements_path = this_directory / "MFT_PRO" / "requirements.txt"
requirements = []
if requirements_path.exists():
    with open(requirements_path, 'r') as f:
        requirements = [
            line.strip() for line in f
            if line.strip() and not line.startswith('#') and not line.startswith('pywebview')
        ]

setup(
    name="mft-professional",
    version="1.0.0",
    author="Your Organization",
    author_email="admin@yourorg.com",
    description="Managed File Transfer System with Enterprise Features",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourorg/mftapp",
    packages=find_packages(where="MFT_PRO"),
    package_dir={"": "MFT_PRO"},
    include_package_data=True,
    install_requires=requirements,
    extras_require={
        'desktop': [
            'pywebview>=4.0.0',
            'pystray>=0.19.0',
            'pillow>=10.0.0',
        ],
        'dev': [
            'pytest>=7.4.0',
            'pytest-cov>=4.1.0',
            'black>=23.7.0',
            'flake8>=6.1.0',
        ],
    },
    entry_points={
        'console_scripts': [
            'mft-server=mft_system_with_rules_ui:main',
            'mft-desktop=mft_launcher_enhanced:main',
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Information Technology",
        "Topic :: System :: Networking",
        "Topic :: System :: Systems Administration",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.8',
    keywords='file transfer mft sftp smb enterprise compliance',
)
