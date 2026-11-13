//! The viewer's persisted settings (the Python interpreter, the language,
//! recent files).

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::i18n::{Lang, Msg, Text};
use crate::worker;

const RECENT_FILES: usize = 10;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    /// The command that runs Python, e.g. `python3`, `py -3`, or the full
    /// path of a virtual environment's interpreter.
    pub interpreter: String,
    /// The most recently opened files, newest first.
    pub recent_files: Vec<String>,
    /// Re-render a view shortly after one of its options changes.
    pub auto_render: bool,
    /// The language of the window; `None` follows the system's locale.
    pub language: Option<Lang>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            interpreter: worker::default_interpreter().to_string(),
            recent_files: Vec::new(),
            auto_render: true,
            language: None,
        }
    }
}

/// `<config dir>/audace-display-gui/settings.json`: `%APPDATA%` on Windows,
/// `$XDG_CONFIG_HOME` or `~/.config` elsewhere.
pub fn settings_path() -> Option<PathBuf> {
    let base = if cfg!(windows) {
        std::env::var_os("APPDATA").map(PathBuf::from)
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|p| p.is_absolute())
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
    }?;
    Some(base.join("audace-display-gui").join("settings.json"))
}

/// `path: error`, for the window.
fn path_error(path: &Path, e: impl std::fmt::Display) -> Text {
    Text::from(Msg::PathError)
        .arg("path", path.display().to_string())
        .arg("error", e.to_string())
}

impl Settings {
    /// The saved settings, or the defaults (with the reason they could not
    /// be read, when a file exists but is unreadable).
    pub fn load_from(path: Option<&Path>) -> (Settings, Option<Text>) {
        let Some(path) = path else {
            return (Settings::default(), None);
        };
        let unreadable = |e: &dyn std::fmt::Display| {
            Text::from(Msg::SettingsUnreadable)
                .arg("path", path.display().to_string())
                .arg("error", e.to_string())
        };
        match std::fs::read(path) {
            Ok(bytes) => match serde_json::from_slice(&bytes) {
                Ok(s) => (s, None),
                Err(e) => (Settings::default(), Some(unreadable(&e))),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => (Settings::default(), None),
            Err(e) => (Settings::default(), Some(unreadable(&e))),
        }
    }

    pub fn save_to(&self, path: Option<&Path>) -> Result<(), Text> {
        let Some(path) = path else {
            return Err(Msg::NoConfigDir.into());
        };
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| path_error(dir, e))?;
        }
        let text = serde_json::to_string_pretty(self).expect("settings serialise");
        // Write then rename: a crash never leaves a half-written file.
        let tmp = path.with_extension("json.tmp");
        std::fs::write(&tmp, text).map_err(|e| path_error(&tmp, e))?;
        std::fs::rename(&tmp, path).map_err(|e| path_error(path, e))
    }

    pub fn remember_file(&mut self, path: &str) {
        self.recent_files.retain(|p| p != path);
        self.recent_files.insert(0, path.to_string());
        self.recent_files.truncate(RECENT_FILES);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir =
            std::env::temp_dir().join(format!("audace-gui-settings-{}-{name}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir.join("sub").join("settings.json")
    }

    #[test]
    fn round_trips_through_the_file() {
        let path = scratch("roundtrip");
        let mut s = Settings {
            interpreter: r#""C:\Py 3\python.exe""#.into(),
            language: Some(Lang::Fr),
            ..Settings::default()
        };
        s.remember_file("/data/a.h5");
        s.save_to(Some(&path)).unwrap();
        let (back, warning) = Settings::load_from(Some(&path));
        assert_eq!(back, s);
        assert!(warning.is_none());
        let _ = std::fs::remove_dir_all(path.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn a_missing_file_gives_the_defaults_quietly() {
        let (s, warning) = Settings::load_from(Some(&scratch("missing")));
        assert_eq!(s, Settings::default());
        assert!(warning.is_none());
    }

    #[test]
    fn a_corrupt_file_gives_the_defaults_and_says_so() {
        let path = scratch("corrupt");
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(&path, b"{not json").unwrap();
        let (s, warning) = Settings::load_from(Some(&path));
        assert_eq!(s, Settings::default());
        assert!(warning.unwrap().render(Lang::En).contains("defaults used"));
        let _ = std::fs::remove_dir_all(path.parent().unwrap().parent().unwrap());
    }

    #[test]
    fn missing_fields_take_their_defaults() {
        let s: Settings = serde_json::from_str(r#"{"interpreter": "python3.12"}"#).unwrap();
        assert_eq!(s.interpreter, "python3.12");
        assert!(s.auto_render);
        assert_eq!(s.language, None, "no language saved: the system's");
    }

    #[test]
    fn the_language_is_saved_by_its_code() {
        let s = Settings {
            language: Some(Lang::Fr),
            ..Settings::default()
        };
        let json = serde_json::to_string(&s).unwrap();
        assert!(json.contains(r#""language":"fr""#), "{json}");
        let back: Settings = serde_json::from_str(r#"{"language": "en"}"#).unwrap();
        assert_eq!(back.language, Some(Lang::En));
        let unknown: Settings = serde_json::from_str(r#"{"language": null}"#).unwrap();
        assert_eq!(unknown.language, None);
    }

    #[test]
    fn recent_files_are_unique_newest_first_and_bounded() {
        let mut s = Settings::default();
        for i in 0..15 {
            s.remember_file(&format!("/f{i}"));
        }
        s.remember_file("/f3");
        assert_eq!(s.recent_files.len(), RECENT_FILES);
        assert_eq!(s.recent_files[0], "/f3");
        assert_eq!(s.recent_files.iter().filter(|p| *p == "/f3").count(), 1);
    }
}
