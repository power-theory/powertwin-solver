from setuptools import setup, find_packages

setup(
    name='solver',
    version='1.0',
    packages=find_packages(),
    py_modules=['cli'],
    install_requires=[
        'requests',
    ],
    entry_points={
        'console_scripts': [
            'solver=cli:main',
        ],
    },
)