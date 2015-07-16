# Citation Network Analyzer
Citation Network Analyzer (*citenet*) is a program for generating and analyzing
compact networks of citation relationships between academic publications.

The full suite consists of:
* a Python application for retrieving a set of academic publications from Google
Scholar, and storing them into a SQLite database.
* a R package containing a series of functions and tools for analysis,
modification and plotting of a set of academic publications and their relationships.

This repository contains the *citenet* Python application.

## Requirements

This module depends on the following packages, available from pip:
* Python 2.7.x
* [PySide 1.2.2](https://pypi.python.org/pypi/PySide)

## Installation

For convenience, a Windows installer package generated with [Nullsoft Scriptable Install System](http://nsis.sourceforge.net/Main_Page) is provided for each release, intended for end users, which includes:
* a compiled version of the *citenet* Python package and its dependencies, using
[PyInstaller](https://github.com/pyinstaller/pyinstaller/wiki).
* an installable version of the [citenet R module](https://github.com/lecy/citation-analysis-in-R) and its dependencies.

The following instructions apply for manual installation via different methods.
### [Virtualenv](https://virtualenv.pypa.io/)

```bash
$ virtualenv venv-citenet
$ cd venv-citenet
$ source bin/activate
(venc-citenet)$ pip install PySide==1.2.2
```
Depending on your setup, the installation of PySide might require that the QT libraries and headers are installed on your system. Please refer to your distribution documentation for more specific instructions.

### Windows

On Windows system, installation instructions might vary depending on your specific version and platform, and the following steps might need to be adjusted to match your environment:

* Download and install [Python 2.7.x](https://www.python.org/downloads/).
* Install pip by downloading [get-pip.py](https://bootstrap.pypa.io/get-pip.py) and executing it.
```bash
python get-pip.py
```
* Install PySide using pip.
```bash
pip install -U PySide
```
Please refer to the [PySide documentation](https://wiki.qt.io/PySide_Binaries_Windows) for more information.

## Launching

Once installed, the Python module can be executed from the top level folder of the project with the following command:
```bash
python -m citenet.citenet
```
Alternatively, it can be invoked from the folder containing the ```scholar.py``` file (```citenet/```) directly (or via double clicking on the ```scholar.py``` file on Windows systems):
```bash
python scholar.py
```

## Additional notes

This application interacts with Google Scholar, performing a series of queries in order to retrieve the publications and related information. **It is the user's sole responsability to ensure that their usage conforms to Google Scholar Terms of Service** and within their acceptable policy and usage limits.

## Changelog
* 1.6.1 (2015-07-16) - Bugfix release (new Scholar settings page, sqlite path defaults to "My Documents")
* 1.6 (2015-05-28) - Initial public release

## License

This software is licensed under the GPL2 license.

```
citenet - Citation Network Analyzer
Copyright (C) 2015 Jesse Lecy <jdlecy@gmail.com>, with contributions from
Diego Moreda <diego.plan9@gmail.com>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License along
with this program; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
```
