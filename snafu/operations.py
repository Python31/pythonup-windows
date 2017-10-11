import atexit
import itertools
import pathlib
import shutil
import tempfile

import click
import requests

from . import configs, metadata, versions


def download_installer(version):
    click.echo('Downloading {}'.format(version.url))
    response = requests.get(version.url, stream=True)
    response.raise_for_status()

    installer_name = version.url.rsplit('/', 1)[-1]
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

    data = b''.join(chunks)

    tempdir_path = pathlib.Path(tempfile.mkdtemp())
    atexit.register(shutil.rmtree, str(tempdir_path), ignore_errors=True)

    installer_path = tempdir_path.joinpath(installer_name)
    version.save_installer(data, installer_path)

    return installer_path


def get_version(name):
    force_32 = not metadata.can_install_64bit()
    try:
        version = versions.get_version(name, force_32=force_32)
    except versions.VersionNotFoundError:
        click.echo('No such version: {}'.format(name), err=True)
        click.get_current_context().exit(1)
    if version.name != name:
        click.echo('Note: Selecting {} instead of {}'.format(
            version.name, name,
        ))
    return version


def check_installed(version, installed, *, on_exit=None):
    if version.is_installed() == installed:
        return
    if installed:
        message = '{} is not installed.'
    else:
        message = '{} is already installed.'
    click.echo(message.format(version), err=True)
    if on_exit:
        on_exit()
    click.get_current_context().exit(1)


def publish_shim(target, content, *, overwrite, quiet):
    if not overwrite and target.exists():
        return
    if not quiet:
        click.echo('  {}'.format(target.name))
    target.with_suffix('.shim').write_text(str(content))
    shutil.copy2(str(configs.get_generic_shim_path()), str(target))


def publish_file(source, target, *, overwrite, quiet):
    if not overwrite and target.exists():
        return
    if not quiet:
        click.echo('  {}'.format(target.name))
    shutil.copy2(str(source), str(target))


def publish_python_command(installation, target, *, overwrite, quiet=False):
    publish_shim(target, installation.python, overwrite=overwrite, quiet=quiet)


def publish_pip_command(installation, target, *, overwrite, quiet=False):
    publish_file(installation.pip, target, overwrite=overwrite, quiet=quiet)


def publish_version_scripts(version, target_dir, *, quiet, overwrite=False):
    if not quiet:
        click.echo('Publishing {}...'.format(version))

    installation = version.get_installation()
    scripts_dir = installation.scripts_dir

    target = target_dir.joinpath('python{}.exe'.format(version.major_version))
    publish_python_command(
        installation, target,
        quiet=quiet, overwrite=overwrite,
    )

    if scripts_dir.is_dir():
        for path in scripts_dir.iterdir():
            if path.stem in ('easy_install', 'pip'):
                # Don't publish versionless pip and easy_install.
                continue
            if not path.is_file():
                continue
            target = target_dir.joinpath(path.name)
            publish_file(path, target, quiet=quiet, overwrite=overwrite)


def activate(versions, *, allow_empty=False, quiet=False):
    if not allow_empty and not versions:
        click.echo('No active versions.', err=True)
        click.get_current_context().exit(1)

    # TODO: Be smarter and calculate diff, not rebuilding every time.
    scripts_dir = configs.get_scripts_dir_path()

    # Remove old stuffs.
    if not quiet:
        click.echo('Removing scripts.')
    for p in scripts_dir.iterdir():
        p.unlink()

    # Populate new stuffs.
    for version in versions:
        publish_version_scripts(version, scripts_dir, quiet=quiet)
    configs.get_python_versions_path().write_text(
        '\n'.join(version.name for version in versions),
    )


def link_commands(version):
    installation = version.get_installation()
    for path in version.python_commands:
        click.echo('Publishing {}'.format(path.name))
        publish_python_command(installation, path, overwrite=True, quiet=True)
    for path in version.pip_commands:
        click.echo('Publishing {}'.format(path.name))
        publish_pip_command(installation, path, overwrite=True, quiet=True)


def safe_unlink(p):
    if p.exists():
        try:
            p.unlink()
        except OSError as e:
            click.echo('Failed to remove {} ({})'.format(p, e), err=True)


def unlink_commands(version):
    for p in itertools.chain(version.python_commands, version.pip_commands):
        click.echo('Unlinking {}'.format(p.name))
        safe_unlink(p)
        safe_unlink(p.with_suffix('.shim'))


def get_active_names():
    try:
        content = configs.get_python_versions_path().read_text()
    except FileNotFoundError:
        return ()
    return tuple(v for v in content.split() if v)


def get_versions(installed_only):
    vers = versions.get_versions()
    names = set(v.name for v in vers)

    def should_include(version):
        if installed_only and not version.is_installed():
            return False
        # On a 32-bit host, hide 64-bit names if there is a 32-bit counterpart.
        if (not metadata.can_install_64bit() and
                not version.name.endswith('-32') and
                '{}-32'.format(version.name) in names):
            return False
        return True

    return [v for v in vers if should_include(v)]


def update_active_versions(*, remove=frozenset()):
    current_active_names = set(get_active_names())
    active_names = [n for n in current_active_names]
    for version in remove:
        try:
            active_names.remove(version.name)
        except ValueError:
            continue
        click.echo('Deactivating {}'.format(version))
    if len(current_active_names) != len(active_names):
        activate([get_version(n) for n in active_names], allow_empty=True)
