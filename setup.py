__author__ = 'katharine'

import sys
from setuptools import setup, find_packages

requires = [
    'libpebble2>=0.0.28',
    'httplib2>=0.19.0',
    'oauth2client>=4.1.3',
    'progressbar2>=2.7.3',
    'pyasn1>=0.1.8',
    'pyasn1-modules>=0.0.6',
    'pypng>=0.20220715.0',
    'pyqrcode>=1.1',
    'requests>=2.32.4',
    'rsa>=4.9.1',
    'pyserial>=3.5',
    'six>=1.17.0',
    'sourcemap>=0.2.0',
    'websocket-client>=1.8.0',
    'wheel>=0.45.1',
    'colorama>=0.3.3',
    'packaging>=25.0',
    'pypkjs>=2.0.0',
    'freetype-py>=2.5.1'
]

if sys.version_info < (3, 4, 0):
    requires.append('enum34==1.0.4')

__version__ = None  # Overwritten by executing version.py.
with open('pebble_tool/version.py') as f:
    exec(f.read())

setup(name='pebble-tool',
      version=__version__,
      description='Tool for interacting with pebbles.',
      url='https://github.com/coredevices/pebble-tool',
      author='Core Devices LLC',
      author_email='griffin@griffinli.com',
      license='MIT',
      packages=find_packages(),
      package_data={
          'pebble_tool.commands.sdk': ['python'],
          'pebble_tool.sdk': ['templates/*'],
          'pebble_tool.util': ['static/**/*', 'static/*.*'],
      },
      install_requires=requires,
      entry_points={
          'console_scripts': ['pebble=pebble_tool:run_tool'],
      },
      zip_safe=False)
