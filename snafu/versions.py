import enum
import hashlib
import json
import os
import pathlib
import subprocess

import attr


class VersionNotFoundError(ValueError):
    pass


class DownloadIntegrityError(ValueError):
    pass


class InstallerType(enum.Enum):
    # Old MSI installer format used by CPython until the 3.4 line.
    # Usage: https://www.python.org/download/releases/2.5/msi/
    cpython_msi = 'cpython_msi'

    # New Python installer introduced in CPython 3.5.
    # Usage: https://docs.python.org/3/using/windows.html#installing-without-ui
    cpython = 'cpython'


@attr.s
class Version:

    name = attr.ib()
    url = attr.ib()
    md5_sum = attr.ib()
    version_info = attr.ib(convert=tuple)

    def save_installer(self, data, into_path):
        checksum = hashlib.md5(data).hexdigest()
        if checksum != self.md5_sum:
            raise DownloadIntegrityError('expect {}, got {}'.format(
                self.md5_sum, checksum,
            ))
        with into_path.open('wb') as f:
            f.write(data)

    def get_install_dir_path(self):
        return pathlib.Path(
            os.environ['LocalAppData'], 'Programs', 'Python',
            'Python{}'.format(self.name.replace('.', '')),
        )


class CPythonMSIVersion(Version):

    def install(self, cmd):
        dirpath = self.get_install_dir_path()
        parts = [
            'msiexec', '/i', '/qb', '"{}"'.format(cmd),
            'ALLUSERS=0', 'TARGETDIR="{}"'.format(dirpath),
            'REMOVE=Extensions,Tools,Testsuite',
        ]
        subprocess.check_call(
            ' '.join(parts),
            shell=True,     # So we don't need to know where msiexec is.
        )
        return dirpath

    def uninstall(self, cmd):
        subprocess.check_call('msiexec /x "{}"'.format(cmd), shell=True)


class CPythonVersion(Version):

    def install(self, cmd):
        dirpath = self.get_install_dir_path()
        subprocess.check_call([
            cmd, '/passive', 'InstallAllUsers=0',
            'DefaultJustForMeTargetDir={}'.format(dirpath),
            'AssociateFiles=0', 'PrependPath=0', 'Shortcuts=0',
            'Include_launcher=0', 'Include_test=0', 'Include_tools=0',
            'InstallLauncherAllUsers=0',
        ])
        return dirpath

    def uninstall(self, cmd):
        subprocess.check_call([cmd, '/uninstall'])


VERSIONS_DIR_PATH = pathlib.Path(__file__).with_name('versions').resolve()


def get_version(name):
    try:
        with VERSIONS_DIR_PATH.joinpath('{}.json'.format(name)).open() as f:
            data = json.load(f)
    except FileNotFoundError:
        raise VersionNotFoundError(name)

    installer_type = InstallerType(data.pop('installer_type'))
    klass = {
        InstallerType.cpython_msi: CPythonMSIVersion,
        InstallerType.cpython: CPythonVersion,
    }[installer_type]

    return klass(name=name, **data)
