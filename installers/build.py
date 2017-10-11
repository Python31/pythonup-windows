import itertools
import json
import pathlib
import shutil
import struct
import subprocess
import zipfile

import click
import pkg_resources
import requests


VERSION = '3.6.3'

DOWNLOAD_PREFIX = 'https://www.python.org/ftp/python'

KB_CODE = 'KB2999226'

WINVERS = [
    '6.0',      # Vista.
    '6.1',      # 7.
    '8-RT',     # 8.
    '8.1',      # 8.1.
]

DLL_NAME = 'vcruntime140.dll'


def get_python_embed_url(architecture):
    return '{pref}/{vers}/python-{vers}-embed-{arch}.zip'.format(
        pref=DOWNLOAD_PREFIX,
        vers=VERSION,
        arch=architecture,
    )


def get_py_launcher_url(architecture):
    # I dug this URL out of Python's webinstaller build.
    # See this part in the build script for description.
    # https://github.com/python/cpython/blob/v3.6.3/Tools/msi/buildrelease.bat
    return '{pref}/{vers}/{arch}/launcher.msi'.format(
        pref=DOWNLOAD_PREFIX,
        vers=VERSION,
        arch=architecture,
    )


def get_kb_msu_url(architecture, wver, warc):
    return '{pref}/{vers}/{arch}/Windows{wver}-{code}-{warc}.msu'.format(
        pref=DOWNLOAD_PREFIX,
        vers=VERSION,
        arch=architecture,
        code=KB_CODE,
        wver=wver,
        warc=warc,
    )


def get_snafu_version():
    with ROOT.parent.joinpath('snafu', '__init__.py').open() as f:
        for line in f:
            if line.startswith('__version__'):
                return eval(line[len('__version__ = '):])


ROOT = pathlib.Path(__file__).parent.resolve()

ASSETSDIR = ROOT.joinpath('assets')
ASSETSDIR.mkdir(exist_ok=True)


def download_file(url, path):
    click.echo('Downloading {}'.format(url))
    response = requests.get(url, stream=True)
    response.raise_for_status()

    installer_name = url.rsplit('/', 1)[-1]
    total = response.headers.get('content-length', '')
    chunks = []

    if total.isdigit():
        length = int(total)
    else:
        length = None
    with click.progressbar(length=length, label=installer_name) as b:
        for chunk in response.iter_content(chunk_size=4096):
            chunks.append(chunk)
            if length is not None:
                b.update(len(chunk))

    path.write_bytes(b''.join(chunks))


def get_py_launcher(arch):
    installer_path = ASSETSDIR.joinpath('py-{vers}-{arch}.msi'.format(
        vers=VERSION,
        arch=arch,
    ))
    if not installer_path.exists():
        download_file(get_py_launcher_url(arch), installer_path)
    return installer_path


def get_embed_bundle(arch):
    url = get_python_embed_url(arch)
    bundle_path = ASSETSDIR.joinpath(url.rsplit('/', 1)[-1])
    if not bundle_path.exists():
        download_file(url, bundle_path)
    return bundle_path


def get_kb_msu(arch, winver, winarc):
    url = get_kb_msu_url(arch, winver, winarc)
    msu_path = ASSETSDIR.joinpath(url.rsplit('/', 1)[-1])
    if not msu_path.exists():
        download_file(url, msu_path)
    return msu_path


def get_dependency_names():
    lock_path = ROOT.parent.joinpath('Pipfile.lock')
    with lock_path.open() as f:
        data = json.load(f)
    return data['default'].keys()


class PackageResolutionError(ValueError):
    pass


def build_package_path(location, name):
    path = pathlib.Path(location, name)
    if path.is_dir():
        return path
    path = pathlib.Path(location, '{}.py'.format(name))
    if path.is_file():
        return path
    raise PackageResolutionError(name)


def get_package_paths():
    # TODO: This only works for pure Python packages.
    # This will fail if we need binary dependencies (e.g. pypiwin32) in the
    # future because the host will only have either 32- or 64-bit binary, but
    # we need both to build installers for each architecture. We should instead
    # download wheels from PyPI, and extract to get the packages.
    paths = []
    for name in get_dependency_names():
        dist = pkg_resources.get_distribution(name)
        top_level = pathlib.Path(dist.egg_info).joinpath('top_level.txt')
        paths.extend(
            build_package_path(dist.location, n)
            for n in top_level.read_text().split('\n') if n
        )
    return paths


def build_python(arch, libdir):
    pythondir = libdir.joinpath('python')
    pythondir.mkdir()

    # Extract Python distribution.
    click.echo('Populating Embeddable Python.')
    with zipfile.ZipFile(str(get_embed_bundle(arch))) as f:
        f.extractall(str(pythondir))

    # Copy SNAFU.
    click.echo('Populate SNAFU.')
    shutil.copytree(
        str(ROOT.parent.joinpath('snafu')),
        str(pythondir.joinpath('snafu')),
    )

    # Write SNAFU configurations.
    with pythondir.joinpath('snafu', 'installation.json').open('w') as f:
        json.dump({
            'cmd_dir': '..\\..\\..\\cmd',
            'scripts_dir': '..\\..\\..\\scripts',
            'shim_source_dir': '..\\..\\shims',
        }, f)

    # Copy dependencies.
    click.echo('Populate dependencies...')
    for path in get_package_paths():
        click.echo('  {}'.format(path.stem))
        if path.is_dir():
            shutil.copytree(str(path), str(pythondir.joinpath(path.name)))
        else:
            shutil.copy2(str(path), str(pythondir.joinpath(path.name)))

    # Cleanup.
    click.echo('Remove junks...')
    for p in pythondir.rglob('__pycache__'):
        shutil.rmtree(str(p))
    for p in pythondir.rglob('*.py[co]'):
        shutil.rmtree(str(p))


def build_snafusetup(arch, libdir):
    snafusetupdir = libdir.joinpath('snafusetup')
    snafusetupdir.mkdir()

    # Copy necessary updates.
    winarcs = {
        'amd64': ['x64'],
        'win32': ['x86', 'x64'],
    }[arch]
    for winver, winarc in itertools.product(WINVERS, winarcs):
        msu_path = get_kb_msu(arch, winver, winarc)
        click.echo('Copy {}'.format(msu_path.name))
        shutil.copy2(
            str(msu_path),
            snafusetupdir.joinpath(msu_path.name),
        )

    # Copy Py launcher MSI.
    click.echo('Copy py.msi')
    msi = get_py_launcher(arch)
    shutil.copy2(str(msi), str(snafusetupdir.joinpath('py.msi')))

    # Copy environment setup script.
    click.echo('Copy env.py')
    shutil.copy2(
        str(ROOT.joinpath('env.py')),
        str(snafusetupdir.joinpath('env.py')),
    )


def build_shims(arch, libdir):
    shimsdir = libdir.joinpath('shims')
    shimsdir.mkdir()
    shimsbasedir = ROOT.parent.joinpath('shims')

    click.echo('Build shim executables')
    subprocess.check_call(
        'cargo clean',
        shell=True, cwd=str(shimsbasedir),
    )
    subprocess.check_call(
        'cargo build --release',
        shell=True, cwd=str(shimsbasedir),
    )
    click.echo('Copy generic.exe')
    shutil.copy2(
        str(shimsbasedir.joinpath('target', 'release', 'generic.exe')),
        str(shimsdir.joinpath('generic.exe')),
    )


def build_lib(arch, container):
    libdir = container.joinpath('lib')
    libdir.mkdir()
    build_python(arch, libdir)
    build_snafusetup(arch, libdir)
    build_shims(arch, libdir)


def build_cmd(container):
    cmddir = container.joinpath('cmd')
    cmddir.mkdir()
    click.echo('Copy snafu.exe')
    shutil.copy2(
        str(container.joinpath('lib', 'shims', 'generic.exe')),
        str(cmddir.joinpath('snafu.exe')),
    )
    # The shim file will be written on installation.

    where_output = subprocess.check_output(['where', DLL_NAME], shell=True)
    dll_path_s = where_output.decode('ascii').split('\n', 1)[0].strip()
    click.echo('Copy {}'.format(dll_path_s))
    shutil.copy2(dll_path_s, str(cmddir))


def build_files(arch):
    container = ROOT.joinpath('snafu')
    if container.exists():
        shutil.rmtree(str(container))
    container.mkdir()
    build_lib(arch, container)
    build_cmd(container)


def build_installer(outpath):
    if outpath.exists():
        outpath.unlink()
    click.echo('Building installer.')
    subprocess.check_call(
        'makensis "{nsi}"'.format(nsi=ROOT.joinpath('snafu.nsi')),
        shell=True,
    )
    click.echo('snafu-setup.exe -> {}'.format(outpath))
    shutil.move(str(ROOT.joinpath('snafu-setup.exe')), str(outpath))


def cleanup():
    container = ROOT.joinpath('snafu')
    shutil.rmtree(str(container))


@click.command()
@click.argument('version', default='dev')
@click.option('--clean/--no-clean', is_flag=True, default=True)
@click.option('--clean-only', is_flag=True)
def build(version, clean, clean_only):
    if clean_only:
        cleanup()
        return
    arch = {
        8: 'amd64',
        4: 'win32',
    }[struct.calcsize('P')]
    out = 'snafu-setup-{}-{}.exe'.format(arch, version.strip())
    outpath = pathlib.Path(out)
    if not outpath.is_absolute():
        outpath = ROOT.joinpath(outpath)

    build_files(arch)
    build_installer(outpath)
    if clean:
        cleanup()


if __name__ == '__main__':
    build()
