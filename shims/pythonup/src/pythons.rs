extern crate winapi;
extern crate winreg;

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use super::tags::Tag;

use self::winapi::shared::minwindef::HKEY;
use self::winreg::RegKey;
use self::winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};


const PYTHON_KEY_PATHS: &[(HKEY, &str); 3] = &[
    (HKEY_CURRENT_USER, "Software\\Python\\PythonCore"),
    (HKEY_LOCAL_MACHINE, "Software\\Python\\PythonCore"),
    (HKEY_LOCAL_MACHINE, "Software\\Wow6432Node\\Python\\PythonCore"),
];

fn get(tag: &Tag) -> Result<PathBuf, String> {
    for &(hkey, rs) in PYTHON_KEY_PATHS {
        let key_path = Path::new(rs).join(tag.to_string()).join("InstallPath");

        let key = match RegKey::predef(hkey).open_subkey(&key_path) {
            Ok(key) => key,
            Err(_) => {
                continue;
            },
        };

        let value: String = try! {
            key.get_value("").map_err(|e| {
                let key_path_string = key_path.to_string_lossy();
                format!("failed to read {}: {}", key_path_string, e)
            })
        };
        return Ok(PathBuf::from(value).join("python.exe"));
    }
    Err(format!("failed to find {}", tag))
}

fn find_installed() -> BTreeSet<Tag> {
    let mut tags = BTreeSet::new();
    for &(hkey, rs) in PYTHON_KEY_PATHS {
        let key = match RegKey::predef(hkey).open_subkey(rs) {
            Ok(key) => key,
            Err(_) => {
                continue;
            },
        };
        for enum_result in key.enum_keys() {
            match enum_result
                    .map_err(|e| e.to_string())
                    .and_then(|n| Tag::parse_strict(&n)) {
                Ok(tag) => {
                    if !tags.contains(&tag) {
                        tags.insert(tag);
                    }
                },
                Err(e) => {
                    eprintln!("ignored entry: {}", e);
                },
            }
        }
    }
    tags
}

/// Find a best Python possible to use.
///
/// This collects all installed Pythons from the registry, and select the best
/// match to the tag. Higher version is better, and the 64-bit is preferred
/// when both 64- and 32-bit are installed, but the tag doesn't specify which.
pub fn find_best_installed(tag: &Tag) -> Result<PathBuf, String> {
    for installed_tag in find_installed().iter().rev() {
        if tag.contains(installed_tag) {
            return get(installed_tag);
        }
    }
    Err(format!("failed to find installed Python for {}", tag))
}

/// Find which of the "using" Pythons should be used.
///
/// This collects "using" Pythons in the registry, set by the "use" command,
/// and look at them one by one until one of those match what the tag asks for.
pub fn find_best_using(tag: &Tag) -> Result<PathBuf, String> {
    let key_path = "Software\\uranusjr\\PythonUp\\ActivePythonVersions";

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let key = try!(hkcu.open_subkey(key_path).map_err(|e| {
        format!("failed to open {}: {}", key_path, e)
    }));
    let value: String = try!(key.get_value("").map_err(|e| {
        format!("failed to read {}: {}", key_path, e)
    }));

    for name in value.split(';') {
        match Tag::parse_strict(name) {
            Ok(ref using_tag) => {
                if tag.contains(using_tag) {
                    return get(using_tag);
                }
            },
            Err(e) => {
                eprintln!("ignored used version: {}", e);
            },
        }
    }
    Err(format!("failed to find used Python for {}", tag))
}

/// Find the Python PythonUp is distributed with.
///
/// This should be the embedded Python library bundled with PythonUp, not one
/// of the user's Python distributions.
pub fn find_of_pythonup() -> Result<PathBuf, String> {
    let key_path = "Software\\uranusjr\\PythonUp\\InstallPath";

    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let key = try!(hkcu.open_subkey(key_path).map_err(|e| {
        format!("failed to open {}: {}", key_path, e)
    }));

    let value: String = try!(key.get_value("").map_err(|e| {
        format!("failed to read {}: {}", key_path, e)
    }));

    let mut path_buf = PathBuf::new();
    path_buf.push(value);
    path_buf.push("lib\\python\\python.exe");
    Ok(path_buf)
}
