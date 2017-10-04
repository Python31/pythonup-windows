import contextlib
import winreg


@contextlib.contextmanager
def open_python_key():
    key = winreg.OpenKey(
        winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER),
        r'Software\Python\PythonCore',
    )
    yield key
    winreg.CloseKey(key)


def get_installed_version_names():
    with open_python_key() as python_key:
        count, _, _ = winreg.QueryInfoKey(python_key)
        return [
            winreg.EnumKey(python_key, i)
            for i in range(count)
        ]


def is_installed(name):
    with open_python_key() as python_key:
        try:
            key = winreg.OpenKey(python_key, r'{}\InstallPath'.format(name))
        except FileNotFoundError:
            return False
        install_path = winreg.QueryValue(key, '')
        winreg.CloseKey(key)
        return bool(install_path)