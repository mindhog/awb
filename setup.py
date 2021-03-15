# Script to build/install the python files in this repo.
#
# This setup script is customized with the ability to compile protobuf
# definition files.  It is expected that a suitable version of "protoc" is
# installed on the path.

from distutils.cmd import Command
from distutils.core import setup
from distutils.command.build_py import build_py
import subprocess

class Protoc(Command):

    description = 'Run the protoc compiler'

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        cmd = ['protoc', '--python_out=.', 'awb.proto']
        self.announce(f'Invoking: {" ".join(cmd)}')
        subprocess.check_call(cmd)

class MyBuildPy(build_py):

    def run(self):
        self.run_command('protoc')
        build_py.run(self)

setup(
    name = 'awb',
    version = '0.1',
    author = 'Michael A. Muller',
    author_email = 'mmuller@enduden.com',
    description = 'Client library for communicating to AWB from python',
    py_modules = [
        'client'
    ],
    cmdclass= {
        'protoc': Protoc,
        'build_py': MyBuildPy,
    },
)
