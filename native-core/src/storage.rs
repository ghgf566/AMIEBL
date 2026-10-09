//! Persistent schema-v1 store and per-user management credential.
//!
//! The runtime API is not wired to this module until the old Python startup,
//! capability detection, persistence and HTTP semantics are all characterized.

use crate::config::{default_config, normalize_startup_config, validate_config};
use chrono::Local;
use serde_json::Value;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use tempfile::NamedTempFile;

pub struct ConfigStore {
    data_dir: PathBuf,
    config: Value,
    admin_token: String,
}

impl ConfigStore {
    pub fn open(data_dir: impl AsRef<Path>) -> Result<Self, String> {
        let data_dir = data_dir.as_ref().to_path_buf();
        fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;
        let path = data_dir.join("config.json");
        let config = if path.exists() {
            let raw = read_json(&path)?;
            let normalized = validate_config(normalize_startup_config(raw.clone()))?;
            if normalized != raw { atomic_json(&path, &normalized)?; }
            normalized
        } else {
            let defaults = validate_config(default_config())?;
            atomic_json(&path, &defaults)?;
            defaults
        };
        let token_path = data_dir.join("admin-token");
        if !token_path.exists() {
            // Create-new rather than truncating if two managers start together.
            let new_token = format!("{}{}", uuid::Uuid::new_v4().simple(),
                                      uuid::Uuid::new_v4().simple());
            match OpenOptions::new().write(true).create_new(true).open(&token_path) {
                Ok(mut file) => {
                    file.write_all(new_token.as_bytes()).map_err(|e| e.to_string())?;
                    file.sync_all().map_err(|e| e.to_string())?;
                }
                Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
                Err(e) => return Err(e.to_string())
            }
        }
        let admin_token = fs::read_to_string(&token_path).map_err(|e| e.to_string())?
            .trim().to_string();
        if admin_token.len() < 24 {
            return Err("管理權杖檔案無效，請移除損毀的 admin-token 後重新啟動。".into());
        }
        Ok(Self { data_dir, config, admin_token })
    }

    pub fn config(&self) -> &Value { &self.config }
    pub fn admin_token(&self) -> &str { &self.admin_token }
    pub fn data_dir(&self) -> &Path { &self.data_dir }

    /// Validate a complete replacement, backing up before committing it.
    /// Future GGUF metadata reconciliation is performed at a higher layer.
    pub fn replace_validated(&mut self, incoming: Value) -> Result<Value, String> {
        let validated = validate_config(incoming)?;
        let backups = self.data_dir.join("backups");
        fs::create_dir_all(&backups).map_err(|e| e.to_string())?;
        let config_path = self.data_dir.join("config.json");
        if config_path.exists() {
            let backup = backups.join(format!("{}-config.json",
                Local::now().format("%Y%m%d-%H%M%S-%6f")));
            fs::copy(&config_path, backup).map_err(|e| e.to_string())?;
        }
        atomic_json(&config_path, &validated)?;
        self.config = validated.clone();
        let mut old_backups = fs::read_dir(&backups).map_err(|e| e.to_string())?
            .flatten().map(|x| x.path()).filter(|p| {
                p.file_name().and_then(|n| n.to_str())
                    .map(|n| n.ends_with("-config.json")).unwrap_or(false)
            }).collect::<Vec<_>>();
        old_backups.sort();
        let remove_count = old_backups.len().saturating_sub(20);
        for old in old_backups.into_iter().take(remove_count) {
            fs::remove_file(&old).map_err(|e| e.to_string())?;
        }
        Ok(validated)
    }
}

pub fn read_json(path: &Path) -> Result<Value, String> {
    let bytes = fs::read(path).map_err(|e| e.to_string())?;
    let source = std::str::from_utf8(&bytes).map_err(|e| e.to_string())?
        .trim_start_matches('\u{feff}');
    serde_json::from_str(source).map_err(|e| e.to_string())
}

pub fn atomic_json(path: &Path, value: &Value) -> Result<(), String> {
    let parent = path.parent().ok_or("無法取得設定檔資料夾。")?;
    fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    let mut temp = NamedTempFile::new_in(parent).map_err(|e| e.to_string())?;
    let mut contents = serde_json::to_string_pretty(value).map_err(|e| e.to_string())?;
    contents.push('\n');
    temp.write_all(contents.as_bytes()).map_err(|e| e.to_string())?;
    temp.as_file().sync_all().map_err(|e| e.to_string())?;
    temp.persist(path).map_err(|e| e.error.to_string())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn empty_config() -> Value {
        let mut c = default_config();
        c["models"] = json!([]);
        c["default_model_id"] = json!("");
        c
    }

    #[test]
    fn fresh_store_creates_valid_config_and_private_token() {
        let folder = tempfile::tempdir().unwrap();
        atomic_json(&folder.path().join("config.json"), &empty_config()).unwrap();
        let first = ConfigStore::open(folder.path()).unwrap();
        assert_eq!(first.config()["schema_version"],1);
        assert_eq!(first.config()["profiles"].as_array().unwrap().len(),3);
        assert_eq!(first.admin_token().len(),64);
        let second = ConfigStore::open(folder.path()).unwrap();
        assert_eq!(first.admin_token(),second.admin_token());
    }

    #[test]
    fn never_overwrites_corrupt_token_or_invalid_config() {
        let folder = tempfile::tempdir().unwrap();
        fs::write(folder.path().join("admin-token"),"short").unwrap();
        atomic_json(&folder.path().join("config.json"),&empty_config()).unwrap();
        assert!(ConfigStore::open(folder.path()).is_err());
        assert_eq!(fs::read_to_string(folder.path().join("admin-token")).unwrap(),"short");
    }

    #[test]
    fn new_settings_preserve_unknown_fields_and_backup_original() {
        let folder = tempfile::tempdir().unwrap();
        atomic_json(&folder.path().join("config.json"),&empty_config()).unwrap();
        let mut store = ConfigStore::open(folder.path()).unwrap();
        let mut next = store.config().clone();
        next["future_extension"] = json!({"keep":true});
        let updated = store.replace_validated(next).unwrap();
        assert_eq!(updated["future_extension"]["keep"],true);
        assert_eq!(read_json(&folder.path().join("config.json")).unwrap(),updated);
        let backups = fs::read_dir(folder.path().join("backups")).unwrap().count();
        assert_eq!(backups,1);
    }

    #[test]
    fn invalid_replacement_keeps_previous_config_untouched() {
        let folder = tempfile::tempdir().unwrap();
        atomic_json(&folder.path().join("config.json"),&empty_config()).unwrap();
        let mut store = ConfigStore::open(folder.path()).unwrap();
        let saved = fs::read(folder.path().join("config.json")).unwrap();
        let mut invalid = store.config().clone();
        invalid["api_port"] = json!(80);
        assert!(store.replace_validated(invalid).is_err());
        assert_eq!(fs::read(folder.path().join("config.json")).unwrap(),saved);
    }
}
