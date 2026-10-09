//! Managed engines are separate from models and externally selected engine_dir.
//! Provider-specific release and launch semantics stay in this module.
use futures_util::StreamExt;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs,
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{process::Command, sync::Mutex};
use tokio_util::sync::CancellationToken;

const API: &str = "https://api.github.com/repos/ggml-org/llama.cpp";
const MAX_ARCHIVE: u64 = 1024 * 1024 * 1024;
const MAX_EXPANDED: u64 = 3 * 1024 * 1024 * 1024;

#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Policy {
    pub channel: String,
    pub backend: String,
    pub update: String,
    pub pinned: bool,
    pub mode: String,
}
impl Default for Policy {
    fn default() -> Self {
        Self {
            channel: "stable".into(),
            backend: "auto".into(),
            update: "notify".into(),
            pinned: false,
            mode: "external".into(),
        }
    }
}
impl Policy {
    pub fn validate(&self) -> Result<(), String> {
        if !matches!(self.channel.as_str(), "stable" | "preview")
            || !matches!(
                self.backend.as_str(),
                "auto" | "cpu" | "cuda12" | "cuda13" | "vulkan" | "sycl" | "openvino" | "rocm"
            )
            || !matches!(self.update.as_str(), "notify" | "download" | "auto" | "off")
            || !matches!(self.mode.as_str(), "external" | "managed")
        {
            return Err("Invalid engine policy".into());
        }
        Ok(())
    }
}
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Asset {
    pub name: String,
    pub url: String,
    pub sha256: String,
    pub size: u64,
}
#[derive(Clone, Serialize, Deserialize, Debug)]
pub struct Package {
    pub id: String,
    pub engine_id: String,
    pub channel: String,
    pub version: String,
    pub build_tag: String,
    pub backend: String,
    pub assets: Vec<Asset>,
    #[serde(default)]
    pub installed_at: Option<String>,
    #[serde(default)]
    pub files: Vec<String>,
    #[serde(default)]
    pub probe: String,
    #[serde(default)]
    pub hashes: std::collections::BTreeMap<String, String>,
}
#[derive(Clone, Serialize, Deserialize)]
struct Manifest {
    schema: u32,
    policy: Policy,
    active: Option<String>,
    previous: Option<String>,
    pending: Option<String>,
    packages: Vec<Package>,
}
impl Default for Manifest {
    fn default() -> Self {
        Self {
            schema: 1,
            policy: Policy::default(),
            active: None,
            previous: None,
            pending: None,
            packages: vec![],
        }
    }
}
struct State {
    manifest: Manifest,
    candidate: Option<Package>,
    job: Value,
    cancel: Option<CancellationToken>,
    hardware: Value,
    checking: bool,
    check_error: String,
    checked_at: Option<u64>,
    activate_pending: bool,
    catalog: Vec<Value>,
}
pub struct EngineManager {
    root: PathBuf,
    state: Mutex<State>,
    operation: Mutex<()>,
    hardware_detection: Mutex<()>,
    client: reqwest::Client,
    write_guard: std::sync::Mutex<Option<fs::File>>,
    initial_manifest_hash: String,
}

fn valid_id(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 160
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn reject_links(path: &Path) -> Result<(), String> {
    for parent in path.ancestors() {
        if let Ok(metadata) = fs::symlink_metadata(parent) {
            #[cfg(windows)]
            {
                use std::os::windows::fs::MetadataExt;
                if metadata.file_attributes() & 0x400 != 0 {
                    return Err("Engine path contains a directory link/reparse point".into());
                }
            }
            if metadata.file_type().is_symlink() {
                return Err("Engine path contains a symbolic link".into());
            }
        }
    }
    Ok(())
}
fn sha(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn verified_asset(asset: &Value) -> Result<Asset, String> {
    let url = asset["browser_download_url"]
        .as_str()
        .ok_or("Missing asset URL")?;
    if !url.starts_with("https://github.com/ggml-org/llama.cpp/releases/download/") {
        return Err("Untrusted package source".into());
    }
    let digest = asset["digest"]
        .as_str()
        .and_then(|s| s.strip_prefix("sha256:"))
        .filter(|s| s.len() == 64 && s.bytes().all(|b| b.is_ascii_hexdigit()))
        .ok_or("Official SHA-256 digest unavailable; refusing unverified installation")?;
    let size = asset["size"]
        .as_u64()
        .filter(|n| *n > 0 && *n <= MAX_ARCHIVE)
        .ok_or("Invalid package size")?;
    Ok(Asset {
        name: asset["name"].as_str().ok_or("Missing asset name")?.into(),
        url: url.into(),
        sha256: digest.to_ascii_lowercase(),
        size,
    })
}

/// Provider boundary: release discovery is separate from generic ZIP installation.
fn package_from_release(
    release: &Value,
    channel: &str,
    version: &str,
    backend: &str,
) -> Result<Package, String> {
    let tag = release["tag_name"]
        .as_str()
        .filter(|s| valid_id(s))
        .ok_or("Invalid upstream build tag")?;
    let suffix = match backend {
        "cpu" => "win-cpu-x64.zip",
        "cuda12" => "win-cuda-12.4-x64.zip",
        "cuda13" => "win-cuda-13.4-x64.zip",
        "vulkan" => "win-vulkan-x64.zip",
        "sycl" => "win-sycl-x64.zip",
        "openvino" => "openvino",
        "rocm" => "rocm",
        _ => return Err("Unsupported backend".into()),
    };
    let all = release["assets"]
        .as_array()
        .ok_or("Missing release assets")?;
    let asset = all
        .iter()
        .find(|a| {
            a["name"].as_str().is_some_and(|n| {
                n.starts_with("llama-")
                    && (if matches!(backend, "openvino" | "rocm") {
                        n.contains(&format!("-win-{backend}-")) && n.ends_with("-x64.zip")
                    } else {
                        n.ends_with(suffix)
                    })
            })
        })
        .ok_or("No matching official Windows x64 package in this channel")?;
    let mut assets = vec![verified_asset(asset)?];
    if backend.starts_with("cuda") {
        let name = format!("cudart-llama-bin-{suffix}");
        let runtime = all
            .iter()
            .find(|a| a["name"] == name)
            .ok_or("Matching CUDA runtime asset missing")?;
        assets.push(verified_asset(runtime)?);
    }
    let id = format!(
        "llama-cpp-{channel}-{}-{tag}-{backend}-x64",
        version.replace('.', "_")
    );
    if !valid_id(&id) {
        return Err("Unsafe package identifier".into());
    }
    Ok(Package {
        id,
        engine_id: "llama.cpp".into(),
        channel: channel.into(),
        version: version.into(),
        build_tag: tag.into(),
        backend: backend.into(),
        assets,
        installed_at: None,
        files: vec![],
        probe: String::new(),
        hashes: std::collections::BTreeMap::new(),
    })
}

// Compatibility is a recommendation, never proof that a particular model will fit.
fn recommend(hardware: &mut Value) {
    let names: Vec<String> = hardware["gpu"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|g| g["Name"].as_str().map(str::to_lowercase))
        .collect();
    let cpu = hardware["cpu"]
        .as_array()
        .and_then(|a| a.first())
        .and_then(|c| c["Name"].as_str())
        .unwrap_or("")
        .to_lowercase();
    let intel = names.iter().any(|n| n.contains("intel"));
    let sycl = names.iter().any(|n| {
        n.contains("intel")
            && (n.contains("iris") && n.contains("xe")
                || n.contains("arc")
                || n.contains("flex")
                || n.contains("data center") && n.contains("max"))
    }) || intel
        && regex::Regex::new(r"i[3579]-(1[1-9]|2[0-9])\d{3}").is_ok_and(|r| r.is_match(&cpu));
    let amd = names
        .iter()
        .any(|n| n.contains("amd") || n.contains("radeon"));
    // Conservative subset of AMD's Windows support matrix. Unlisted devices remain manual.
    let rocm_model = names.iter().any(|n| {
        [
            "rx 9070",
            "rx 9060",
            "rx 7900",
            "rx 7800 xt",
            "rx 7700 xt",
            "rx 7600",
            "pro w7900",
            "pro w7800",
            "pro w7700",
            "ai pro r9700",
        ]
        .iter()
        .any(|model| n.contains(model))
    });
    let windows_build = hardware["windows_build"]
        .as_str()
        .and_then(|n| n.parse::<u32>().ok())
        .unwrap_or(0);
    let amd_driver = hardware["amd_software_version"]
        .as_str()
        .map(|n| {
            n.split('.')
                .filter_map(|p| p.parse::<u32>().ok())
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    let rocm = rocm_model
        && windows_build >= 26200
        && amd_driver.len() >= 3
        && (amd_driver[0], amd_driver[1], amd_driver[2]) >= (26, 8, 1);
    let devices = hardware["nvidia_devices"].as_array();
    let cc = devices.and_then(|a| {
        a.iter()
            .filter_map(|g| g["compute_capability"].as_f64())
            .reduce(f64::min)
    });
    let highest_cc = devices.and_then(|a| {
        a.iter()
            .filter_map(|g| g["compute_capability"].as_f64())
            .reduce(f64::max)
    });
    let driver = hardware["cuda_driver_max"]
        .as_str()
        .and_then(|v| v.split_once('.'))
        .and_then(|(a, b)| Some((a.parse::<u32>().ok()?, b.parse::<u32>().ok()?)))
        .unwrap_or((0, 0));
    let cuda12 =
        cc.is_some_and(|v| v >= 5.0) && highest_cc.is_some_and(|v| v < 10.0) && driver >= (12, 4);
    let cuda13 = cc.is_some_and(|v| v >= 7.5) && driver >= (13, 4);
    let recommendation = if cuda13 {
        "cuda13"
    } else if cuda12 {
        "cuda12"
    } else if sycl {
        "sycl"
    } else if rocm {
        "rocm"
    } else if amd {
        "vulkan"
    } else {
        "cpu"
    };
    let reason = match recommendation {
        "cuda12" => "建議 CUDA 12.4：已比對 GPU Compute Capability 與驅動；Pascal／Maxwell／Volta 不使用 CUDA 13。",
        "cuda13" => "建議 CUDA 13.4：GPU 為 Turing 或更新架構，且驅動支援此版本。",
        "sycl" => "建議 Intel SYCL：偵測到支援範圍內的 Intel 核顯／Arc；仍需套件裝置驗證及足夠共享記憶體。80 EU 是效能建議，非硬性相容門檻。",
        "rocm" => "建議 ROCm 10.0：型號符合已知 Windows 支援表，Windows 至少 11 25H2，Adrenalin 至少 26.8.1；安裝後仍需驗證 HIP 裝置。",
        "vulkan" => "建議先驗證 Vulkan：偵測到 AMD GPU；ROCm 需要另外確認套件版本、Windows 支援表與 Adrenalin 驅動，不只比對 Radeon 品牌。",
        _ => "建議 CPU：尚未確認相容的 GPU 套件；可手動選擇其他後端並檢視限制。"
    };
    hardware["recommendation"] = json!(recommendation);
    hardware["reason"] = json!(reason);
    hardware["backends"] = json!([
        {"id":"cpu","eligible":true,"reason":"Windows x64；官方 CPU 套件依處理器選擇指令集，模型受系統記憶體限制。"},
        {"id":"cuda12","eligible":cuda12,"reason":"CUDA 12.4：需相容 NVIDIA 驅動及套件 GPU 架構；GTX 1070 Ti（6.1）使用此通道。Blackwell 優先使用新套件，舊版需另驗證 PTX 與函式庫。"},
        {"id":"cuda13","eligible":cuda13,"reason":"CUDA 13.4：需 Turing／Compute Capability 7.5 以上及相容驅動；不支援 Maxwell、Pascal、Volta。"},
        {"id":"sycl","eligible":sycl,"reason":"Intel SYCL：第 11 代以上 Core 核顯、Arc／Flex／Max，需 Intel GPU 驅動。Windows 套件包含 oneAPI 執行期；核顯低於 80 EU 可能效能不足。"},
        {"id":"openvino","eligible":cpu.contains("intel") || intel,"reason":"OpenVINO：Intel CPU／GPU；GPU 需相容驅動。各模型與運算子支援仍需驗證；NPU 必須另行確認，不能从 Intel 品牌推定。"},
        {"id":"rocm","eligible":rocm,"reason":if amd && !rocm { "ROCm 10.0：需列出的 GPU 型號、Windows 11 25H2（build 26200）與 Adrenalin 26.8.1 以上；目前至少一項不符合或未確認。可選 Vulkan，或手動下載測試。" } else { "ROCm／HIP：需符合該套件版本的 Windows GPU、驅動與系統支援表；不是所有 Radeon 都支援，下載後仍需裝置驗證。" }},
        {"id":"vulkan","eligible":!names.is_empty(),"reason":"Vulkan：需 GPU 驅動提供 Vulkan Compute；偵測到顯示卡不代表已驗證 Vulkan 能力，安裝後需測試。"}
    ]);
}

impl EngineManager {
    pub fn open(app_directory: &Path) -> Result<Arc<Self>, String> {
        let root = app_directory.join("engines");
        let legacy = app_directory.join("推理引擎");
        reject_links(&legacy)?;
        if legacy.exists() && !root.exists() {
            // Preserve all existing contents; refuse migration while another manager writes.
            let lock_path = legacy.join("manifest.lock");
            reject_links(&lock_path)?;
            let lock = fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .write(true)
                .open(lock_path)
                .map_err(|e| e.to_string())?;
            lock.try_lock()
                .map_err(|_| "Close the previous AMIEBL before migrating engines")?;
            drop(lock); // Windows cannot rename a directory with an open locked child.
            fs::rename(&legacy, &root)
                .map_err(|e| format!("Cannot migrate engine directory: {e}"))?;
        }
        reject_links(&root)?;
        let initial_manifest_hash = fs::read(root.join("manifest.json"))
            .map(|b| sha(&b))
            .unwrap_or_default();
        let manifest = if root.join("manifest.json").exists() {
            let m: Manifest = serde_json::from_slice(
                &fs::read(root.join("manifest.json")).map_err(|e| e.to_string())?,
            )
            .map_err(|e| format!("Engine manifest is invalid: {e}"))?;
            if m.schema != 1
                || m.packages
                    .iter()
                    .any(|p| !valid_id(&p.id) || p.files.iter().any(|f| !safe_relative(f)))
            {
                return Err("Unsafe engine manifest".into());
            }
            m.policy.validate()?;
            m
        } else {
            Manifest::default()
        };
        let client = reqwest::Client::builder()
            .https_only(true)
            .user_agent("AMIEBL/1.1.0 engine-manager")
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(900))
            .build()
            .map_err(|e| e.to_string())?;
        Ok(Arc::new(Self {
            root,
            state: Mutex::new(State {
                manifest,
                candidate: None,
                job: json!({"state":"idle"}),
                cancel: None,
                hardware: Value::Null,
                checking: false,
                check_error: String::new(),
                checked_at: None,
                activate_pending: false,
                catalog: vec![],
            }),
            operation: Mutex::new(()),
            hardware_detection: Mutex::new(()),
            client,
            write_guard: std::sync::Mutex::new(None),
            initial_manifest_hash,
        }))
    }
    fn write_manifest(&self, manifest: &Manifest) -> Result<(), String> {
        reject_links(&self.root)?;
        fs::create_dir_all(&self.root)
            .map_err(|e| format!("Engine directory is not writable: {e}"))?;
        // Lock across processes; released by the OS on a crash. Never relax installation ACLs.
        let lock_path = self.root.join("manifest.lock");
        reject_links(&lock_path)?;
        let mut guard = self.write_guard.lock().map_err(|e| e.to_string())?;
        if guard.is_none() {
            let lock = fs::OpenOptions::new()
                .create(true)
                .truncate(false)
                .write(true)
                .open(lock_path)
                .map_err(|e| e.to_string())?;
            lock.try_lock().map_err(|_| {
                "Another AMIEBL process owns engine management; restart after it exits"
            })?;
            // Refuse to replace another process's newer manifest with a stale in-memory copy.
            let hash = fs::read(self.root.join("manifest.json"))
                .map(|b| sha(&b))
                .unwrap_or_default();
            if hash != self.initial_manifest_hash {
                return Err(
                    "Engine metadata changed in another process; restart to reload it".into(),
                );
            }
            *guard = Some(lock);
        }
        reject_links(&self.root.join("manifest.json"))?;
        crate::storage::atomic_json(
            &self.root.join("manifest.json"),
            &serde_json::to_value(manifest).map_err(|e| e.to_string())?,
        )
    }
    pub async fn status(&self) -> Value {
        let s = self.state.lock().await;
        json!({"root":self.root,"policy":s.manifest.policy,"active":s.manifest.active,"previous":s.manifest.previous,"pending":s.manifest.pending,"packages":s.manifest.packages,"candidate":s.candidate,"job":s.job,"hardware":s.hardware,"checking":s.checking,"check_error":s.check_error,"checked_at":s.checked_at,"catalog":s.catalog})
    }
    pub async fn policy(&self, policy: Policy) -> Result<Value, String> {
        policy.validate()?;
        let _op = self
            .operation
            .try_lock()
            .map_err(|_| "An engine operation is already running")?;
        let mut s = self.state.lock().await;
        if s.cancel.is_some() {
            return Err("An engine installation is running".into());
        }
        let mut next = s.manifest.clone();
        next.policy = policy;
        s.activate_pending = false;
        next.pending = None;
        self.write_manifest(&next)?;
        s.manifest = next;
        s.candidate = None;
        drop(s);
        Ok(self.status().await)
    }
    pub async fn detect(&self) -> Result<Value, String> {
        let _detection = self.hardware_detection.lock().await;
        let cached = self.state.lock().await.hardware.clone();
        if !cached.is_null() {
            return Ok(cached);
        }
        let mut cmd = Command::new("powershell.exe");
        #[cfg(windows)]
        {
            cmd.creation_flags(0x08000000);
        }
        cmd.kill_on_drop(true).args(["-NoProfile", "-NonInteractive", "-Command", "$ErrorActionPreference='Stop'; $cpu=Get-CimInstance Win32_Processor; $g=Get-CimInstance Win32_VideoController; $ram=(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory; $os=(Get-CimInstance Win32_OperatingSystem).BuildNumber; $amd=$null; if(Test-Path -LiteralPath 'HKLM:\\SOFTWARE\\AMD\\CN') { $amd=(Get-ItemProperty -LiteralPath 'HKLM:\\SOFTWARE\\AMD\\CN').AMDSoftwareVersion };  @{windows_build=$os;amd_software_version=$amd;cpu=@($cpu|Select-Object Name,NumberOfCores,NumberOfLogicalProcessors);gpu=@($g|Select-Object Name,AdapterCompatibility,DriverVersion);ram_bytes=$ram}|ConvertTo-Json -Depth 5 -Compress"]);
        let result = tokio::time::timeout(Duration::from_secs(20), cmd.output()).await;
        let mut hardware = match result {
            Ok(Ok(o)) if o.status.success() => serde_json::from_slice::<Value>(&o.stdout)
                .unwrap_or(json!({"error":"Hardware response could not be parsed"})),
            _ => json!({"error":"Hardware detection unavailable; manually select a backend"}),
        };
        hardware["architecture"] = json!(std::env::consts::ARCH);
        hardware["os"] = json!(std::env::consts::OS);
        #[cfg(target_arch = "x86_64")]
        {
            hardware["avx2"] = json!(std::is_x86_feature_detected!("avx2"));
        }
        let mut gpu = Command::new("nvidia-smi");
        #[cfg(windows)]
        {
            gpu.creation_flags(0x08000000);
        }
        gpu.kill_on_drop(true);
        if let Ok(Ok(output)) = tokio::time::timeout(Duration::from_secs(5), gpu.output()).await {
            if output.status.success() {
                let text = String::from_utf8_lossy(&output.stdout);
                let regex = regex::Regex::new(r"CUDA Version:\s*(\d+)\.(\d+)")
                    .map_err(|e| e.to_string())?;
                if let Some(version) = regex.captures(&text) {
                    hardware["cuda_driver_max"] = json!(format!("{}.{}", &version[1], &version[2]));
                }
            }
        }
        let mut query = Command::new("nvidia-smi");
        #[cfg(windows)]
        {
            query.creation_flags(0x08000000);
        }
        query
            .kill_on_drop(true)
            .args(["--query-gpu=name,compute_cap", "--format=csv,noheader"]);
        if let Ok(Ok(output)) = tokio::time::timeout(Duration::from_secs(5), query.output()).await {
            if output.status.success() {
                hardware["nvidia_devices"] = json!(String::from_utf8_lossy(&output.stdout).lines().filter_map(|line| {
                    let (name, cc) = line.rsplit_once(',')?;
                    Some(json!({"name":name.trim(),"compute_capability":cc.trim().parse::<f64>().ok()?}))
                }).collect::<Vec<_>>());
            }
        }
        recommend(&mut hardware);
        self.state.lock().await.hardware = hardware.clone();
        Ok(hardware)
    }
    async fn get_json(&self, url: &str) -> Result<Value, String> {
        let response = self
            .client
            .get(url)
            .send()
            .await
            .map_err(|e| e.to_string())?
            .error_for_status()
            .map_err(|e| e.to_string())?;
        response.json().await.map_err(|e| e.to_string())
    }
    pub async fn check(&self) -> Result<Value, String> {
        let _op = self
            .operation
            .try_lock()
            .map_err(|_| "An engine operation is already running")?;
        {
            let mut s = self.state.lock().await;
            s.checking = true;
            s.check_error.clear();
        }
        let result = self.check_inner().await;
        {
            let mut s = self.state.lock().await;
            s.checking = false;
            s.checked_at = Some(
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs(),
            );
            s.check_error = result.as_ref().err().cloned().unwrap_or_default();
        }
        result?;
        Ok(self.status().await)
    }
    async fn check_inner(&self) -> Result<Value, String> {
        if std::env::consts::OS != "windows" || std::env::consts::ARCH != "x86_64" {
            return Err("Managed packages currently support Windows x64 only".into());
        }
        let policy = self.state.lock().await.manifest.policy.clone();
        let hardware = self.detect().await?;
        let (release, version) = if policy.channel == "preview" {
            let releases = self
                .get_json(&format!("{API}/releases?per_page=30"))
                .await?;
            let release = releases
                .as_array()
                .and_then(|a| {
                    a.iter()
                        .find(|r| r["draft"] == false && r["prerelease"] == true)
                })
                .ok_or("Preview channel is unavailable")?
                .clone();
            let version = release["tag_name"]
                .as_str()
                .ok_or("Invalid preview version")?
                .to_owned();
            (release, version)
        } else {
            let release = self.get_json(&format!("{API}/releases/latest")).await?;
            if release["prerelease"] != false || release["draft"] != false {
                return Err("Stable release unavailable".into());
            }
            let version = release["tag_name"]
                .as_str()
                .filter(|v| {
                    v.starts_with('v')
                        && v[1..].split('.').count() == 3
                        && v[1..].split('.').all(|n| n.parse::<u32>().is_ok())
                })
                .ok_or("Stable semantic version unavailable")?
                .to_owned();
            if let Some(pointer) = release["assets"]
                .as_array()
                .and_then(|a| a.iter().find(|a| a["name"] == "nightly-tag.txt"))
            {
                let pointer = verified_asset(pointer)?;
                if pointer.size > 100 {
                    return Err("Invalid stable build pointer".into());
                }
                let bytes = self
                    .client
                    .get(&pointer.url)
                    .send()
                    .await
                    .map_err(|e| e.to_string())?
                    .error_for_status()
                    .map_err(|e| e.to_string())?
                    .bytes()
                    .await
                    .map_err(|e| e.to_string())?;
                if bytes.len() as u64 != pointer.size || sha(&bytes) != pointer.sha256 {
                    return Err("Stable build pointer verification failed".into());
                }
                let tag = std::str::from_utf8(&bytes)
                    .map_err(|_| "Invalid build pointer")?
                    .trim();
                if !tag.starts_with('b') || !valid_id(tag) {
                    return Err("Unsafe stable build pointer".into());
                }
                (
                    self.get_json(&format!("{API}/releases/tags/{tag}")).await?,
                    version,
                )
            } else {
                (release, version)
            }
        };
        let mut catalog = Vec::new();
        for id in [
            "cpu", "cuda12", "cuda13", "sycl", "openvino", "rocm", "vulkan",
        ] {
            let info = hardware["backends"]
                .as_array()
                .and_then(|a| a.iter().find(|o| o["id"] == id))
                .cloned()
                .unwrap_or(json!({"id":id,"eligible":false}));
            let mut entry = info;
            match package_from_release(&release, &policy.channel, &version, id) {
                Ok(package) => {
                    let asset = &package.assets[0];
                    entry["available"] = json!(true);
                    entry["asset"] = json!(asset.name);
                    entry["bytes"] = json!(package.assets.iter().map(|a| a.size).sum::<u64>());
                    let toolkit = match id {
                        "cuda12" => "12.4".to_string(),
                        "cuda13" => "13.4".to_string(),
                        "rocm" | "openvino" => asset
                            .name
                            .split(&format!("-win-{id}-"))
                            .nth(1)
                            .and_then(|v| v.strip_suffix("-x64.zip"))
                            .unwrap_or("unknown")
                            .to_string(),
                        _ => String::new(),
                    };
                    entry["toolkit_version"] = json!(toolkit);
                    if id == "rocm" {
                        entry["reason"] = json!(format!("ROCm {toolkit} Windows 套件：GPU 型號僅為初步篩選；需比對同版支援表、Windows 與 Adrenalin 驅動。{}", entry["reason"].as_str().unwrap_or("")));
                        if toolkit != "10.0" {
                            entry["eligible"] = json!(false);
                            entry["reason"] = json!("此 ROCm 套件版本尚未建立 Windows 相容性規則；請使用外部引擎或 Vulkan。");
                        }
                    }
                    if id == "openvino" {
                        entry["reason"] = json!(format!(
                            "OpenVINO {toolkit} Windows 套件 · {}",
                            entry["reason"].as_str().unwrap_or("")
                        ));
                    }
                }
                Err(error) => {
                    entry["available"] = json!(false);
                    entry["availability_reason"] = json!(error);
                }
            }
            catalog.push(entry);
        }
        self.state.lock().await.catalog = catalog.clone();
        let recommended = hardware["recommendation"].as_str().unwrap_or("cpu");
        let backend = if policy.backend == "auto" {
            if catalog
                .iter()
                .any(|e| e["id"] == recommended && e["available"] == true && e["eligible"] == true)
            {
                recommended
            } else {
                "cpu"
            }
        } else {
            &policy.backend
        };
        if let Some(option) = catalog.iter().find(|o| o["id"] == backend) {
            if option["available"] != true {
                return Err(option["availability_reason"]
                    .as_str()
                    .unwrap_or("No official Windows package in this release")
                    .to_string());
            }
            if option["eligible"] != true
                && (policy.backend == "auto" || backend.starts_with("cuda"))
            {
                return Err(option["reason"]
                    .as_str()
                    .unwrap_or("Hardware compatibility is unverified")
                    .to_string());
            }
        }
        if backend == "cuda13"
            && hardware["nvidia_devices"].as_array().is_some_and(|a| {
                a.iter()
                    .any(|g| g["compute_capability"].as_f64().is_some_and(|cc| cc < 7.5))
            })
        {
            return Err("CUDA 13 不支援 Maxwell／Pascal／Volta；請選 CUDA 12.4。".into());
        }
        if backend.starts_with("cuda") {
            let max = hardware["cuda_driver_max"].as_str().ok_or(
                "No usable NVIDIA CUDA driver was detected; choose CPU or inspect the driver",
            )?;
            let parts: Vec<u32> = max.split('.').filter_map(|n| n.parse().ok()).collect();
            let required = if backend == "cuda13" {
                (13, 4)
            } else {
                (12, 4)
            };
            if parts.len() != 2 || (parts[0], parts[1]) < required {
                return Err("NVIDIA driver does not support the selected CUDA package".into());
            }
        }
        let package = package_from_release(&release, &policy.channel, &version, backend)?;
        self.state.lock().await.candidate = Some(package);
        Ok(self.status().await)
    }
    pub async fn install(self: &Arc<Self>) -> Result<Value, String> {
        let mut s = self.state.lock().await;
        if s.cancel.is_some() {
            return Err("An installation is already running".into());
        }
        let package = s
            .candidate
            .clone()
            .ok_or("Check the selected channel before installing")?;
        if s.manifest.packages.iter().any(|p| p.id == package.id) {
            return Err("This version is already installed; activate it instead".into());
        }
        let cancel = CancellationToken::new();
        s.cancel = Some(cancel.clone());
        s.job = json!({"state":"starting","package_id":package.id,"bytes":0});
        drop(s);
        let manager = self.clone();
        tokio::spawn(async move {
            let result = manager.install_package(package, cancel.clone()).await;
            let mut s = manager.state.lock().await;
            s.job = match result {
                Ok(id) => json!({"state":"installed","package_id":id}),
                Err(e) => {
                    json!({"state":if cancel.is_cancelled(){"cancelled"}else{"failed"},"error":e})
                }
            };
            s.cancel = None;
        });
        Ok(self.status().await)
    }
    async fn install_package(
        &self,
        mut package: Package,
        cancel: CancellationToken,
    ) -> Result<String, String> {
        let _operation = self.operation.lock().await;
        reject_links(&self.root)?;
        let versions = self.root.join("llama.cpp/versions");
        let staging = self.root.join("llama.cpp/staging");
        reject_links(&versions)?;
        reject_links(&staging)?;
        fs::create_dir_all(&staging)
            .map_err(|e| format!("Engine directory requires write permission: {e}"))?;
        fs::create_dir_all(&versions).map_err(|e| e.to_string())?;
        {
            let s = self.state.lock().await;
            self.write_manifest(&s.manifest)?;
        }
        let required = package.assets.iter().map(|a| a.size).sum::<u64>() + MAX_EXPANDED;
        if available_space(&self.root)? < required {
            return Err("Insufficient disk space for download and extraction".into());
        }
        let final_dir = versions.join(&package.id);
        if final_dir.exists() {
            return Err("Version directory already exists; preserving existing files".into());
        }
        let stage = tempfile::Builder::new()
            .prefix("install-")
            .tempdir_in(&staging)
            .map_err(|e| e.to_string())?;
        // Installation only writes within this newly created private staging directory.
        let payload = stage.path().join("payload");
        fs::create_dir(&payload).map_err(|e| e.to_string())?;
        let total = package.assets.iter().map(|a| a.size).sum::<u64>();
        let mut completed = 0u64;
        for (index, asset) in package.assets.iter().enumerate() {
            if cancel.is_cancelled() {
                return Err("Installation cancelled".into());
            }
            let response = self
                .client
                .get(&asset.url)
                .send()
                .await
                .map_err(|e| e.to_string())?
                .error_for_status()
                .map_err(|e| e.to_string())?;
            let archive = stage.path().join(format!("{index}.zip"));
            let mut file = fs::File::create(&archive).map_err(|e| e.to_string())?;
            let mut hash = Sha256::new();
            let mut downloaded = 0u64;
            let mut stream = response.bytes_stream();
            loop {
                let chunk = tokio::select! { _ = cancel.cancelled() => return Err("Installation cancelled".into()), chunk = stream.next() => chunk };
                let Some(chunk) = chunk else { break };
                let chunk = chunk.map_err(|e| e.to_string())?;
                downloaded += chunk.len() as u64;
                if downloaded > asset.size || downloaded > MAX_ARCHIVE {
                    return Err("Package exceeded advertised size".into());
                }
                hash.update(&chunk);
                file.write_all(&chunk).map_err(|e| e.to_string())?;
                self.state.lock().await.job = json!({"state":"downloading","package_id":package.id,"asset":asset.name,"bytes":completed+downloaded,"total":total});
            }
            file.sync_all().map_err(|e| e.to_string())?;
            drop(file);
            if downloaded != asset.size || format!("{:x}", hash.finalize()) != asset.sha256 {
                return Err("Official package SHA-256/size mismatch".into());
            }
            self.state.lock().await.job = json!({"state":"extracting","package_id":package.id});
            let destination = payload.clone();
            let token = cancel.clone();
            tokio::task::spawn_blocking(move || extract(&archive, &destination, &token))
                .await
                .map_err(|e| e.to_string())??;
            completed += asset.size;
        }
        if cancel.is_cancelled() {
            return Err("Installation cancelled".into());
        }
        self.state.lock().await.job = json!({"state":"validating","package_id":package.id});
        let executable = find_server(&payload)?;
        package.probe = probe(&executable).await?;
        package
            .probe
            .push_str(&validate_backend(&executable, &package.backend).await?);
        // Existing AMIEBL options include llama-fit-params; never silently drop them.
        package.files = inventory(&payload)?;
        for file in &package.files {
            package
                .hashes
                .insert(file.clone(), hash_file(&payload.join(file))?);
        }
        if cancel.is_cancelled() {
            return Err("Installation cancelled".into());
        }
        reject_links(&final_dir)?;
        let mut s = self.state.lock().await;
        let mut next = s.manifest.clone();
        package.installed_at = Some(chrono::Utc::now().to_rfc3339());
        let id = package.id.clone();
        fs::rename(&payload, &final_dir).map_err(|e| e.to_string())?;
        next.packages.push(package);
        next.pending = Some(id.clone());
        if let Err(e) = self.write_manifest(&next) {
            // Keep the owned, unreferenced version rather than risking broad deletion.
            return Err(format!(
                "Package saved but metadata could not be committed: {e}"
            ));
        }
        s.manifest = next;
        Ok(id)
    }
    pub async fn cancel(&self) -> Value {
        if let Some(c) = &self.state.lock().await.cancel {
            c.cancel();
        }
        self.status().await
    }
    pub async fn directory(&self, id: &str) -> Result<PathBuf, String> {
        if !valid_id(id) {
            return Err("Invalid package id".into());
        }
        if !self
            .state
            .lock()
            .await
            .manifest
            .packages
            .iter()
            .any(|p| p.id == id)
        {
            return Err("Version is not managed by AMIEBL".into());
        }
        let root = self.root.join("llama.cpp/versions").join(id);
        reject_links(&root)?;
        let package = self
            .state
            .lock()
            .await
            .manifest
            .packages
            .iter()
            .find(|p| p.id == id)
            .cloned()
            .ok_or("Unknown package")?;
        if package.hashes.is_empty() {
            return Err(
                "Package file verification metadata is missing; reinstall this version".into(),
            );
        }
        for file in &package.files {
            let path = root.join(file);
            reject_links(&path)?;
            if package.hashes.get(file) != Some(&hash_file(&path)?) {
                return Err("Installed engine file was changed or is missing; preserving it without activation".into());
            }
        }
        let executable = find_server(&root)?;
        reject_links(&executable)?;
        Ok(executable
            .parent()
            .ok_or("Missing executable directory")?
            .to_owned())
    }
    pub async fn activate(&self, id: &str) -> Result<(), String> {
        let _op = self
            .operation
            .try_lock()
            .map_err(|_| "An engine operation is running")?;
        let directory = self.directory(id).await?;
        probe(&directory.join("llama-server.exe")).await?;
        let backend = self
            .state
            .lock()
            .await
            .manifest
            .packages
            .iter()
            .find(|p| p.id == id)
            .ok_or("Unknown package")?
            .backend
            .clone();
        validate_backend(&directory.join("llama-server.exe"), &backend).await?;
        let mut s = self.state.lock().await;
        let mut next = s.manifest.clone();
        if next.active.as_deref() != Some(id) {
            next.previous = next.active.clone();
            next.active = Some(id.into());
        }
        next.pending = None;
        next.policy.mode = "managed".into();
        self.write_manifest(&next)?;
        s.manifest = next;
        s.activate_pending = false;
        Ok(())
    }
    pub async fn previous(&self) -> Result<String, String> {
        self.state
            .lock()
            .await
            .manifest
            .previous
            .clone()
            .ok_or("No rollback version".into())
    }
    pub async fn active_directory(&self) -> Result<Option<PathBuf>, String> {
        let s = self.state.lock().await;
        if s.manifest.policy.mode != "managed" {
            return Ok(None);
        }
        let id = s.manifest.active.clone();
        drop(s);
        match id {
            Some(id) => self.directory(&id).await.map(Some),
            None => Ok(None),
        }
    }
    pub async fn request_activation(&self) {
        self.state.lock().await.activate_pending = true;
    }
    pub async fn auto_candidate(&self) -> Option<String> {
        let s = self.state.lock().await;
        if s.manifest.policy.mode == "managed"
            && (s.activate_pending
                || s.manifest.policy.update == "auto" && !s.manifest.policy.pinned)
        {
            s.manifest.pending.clone()
        } else {
            None
        }
    }
    pub async fn automatic_check(self: &Arc<Self>) -> Result<(), String> {
        let policy = self.state.lock().await.manifest.policy.clone();
        if policy.mode != "managed" || policy.pinned || policy.update == "off" {
            return Ok(());
        }
        self.check().await?;
        if matches!(policy.update.as_str(), "download" | "auto") {
            let s = self.state.lock().await;
            let installed = s
                .candidate
                .as_ref()
                .is_some_and(|c| s.manifest.packages.iter().any(|p| p.id == c.id));
            drop(s);
            if !installed {
                self.install().await?;
            }
        }
        Ok(())
    }
    pub async fn remove(&self, id: &str) -> Result<Value, String> {
        let _op = self
            .operation
            .try_lock()
            .map_err(|_| "An engine operation is running")?;
        self.directory(id).await?;
        let mut s = self.state.lock().await;
        let package = s
            .manifest
            .packages
            .iter()
            .find(|p| p.id == id)
            .ok_or("Unknown package")?
            .clone();
        let root = self.root.join("llama.cpp/versions").join(id);
        let files = inventory(&root)?;
        if files.iter().any(|p| !package.files.contains(p)) {
            return Err("Unknown files exist in this version directory; preserving it".into());
        }
        self.write_manifest(&s.manifest)?;
        for file in &files {
            let path = root.join(file);
            reject_links(&path)?;
            fs::remove_file(path).map_err(|e| e.to_string())?;
        }
        remove_empty_directories(&root)?;
        let mut next = s.manifest.clone();
        next.packages.retain(|p| p.id != id);
        if next.active.as_deref() == Some(id) {
            next.active = None;
        }
        if next.previous.as_deref() == Some(id) {
            next.previous = None;
        }
        if next.pending.as_deref() == Some(id) {
            next.pending = None;
        }
        if next.packages.is_empty() {
            // An explicit removal of every package must not trigger automatic reinstallation.
            next.policy.update = "off".into();
            s.activate_pending = false;
        }
        self.write_manifest(&next)?;
        s.manifest = next;
        drop(s);
        Ok(self.status().await)
    }
}

fn remove_empty_directories(root: &Path) -> Result<(), String> {
    reject_links(root)?;
    for e in fs::read_dir(root).map_err(|e| e.to_string())? {
        let path = e.map_err(|e| e.to_string())?.path();
        reject_links(&path)?;
        if path.is_dir() {
            remove_empty_directories(&path)?;
        }
    }
    fs::remove_dir(root).map_err(|e| e.to_string())
}
fn available_space(path: &Path) -> Result<u64, String> {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::OsStrExt;
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn GetDiskFreeSpaceExW(
                path: *const u16,
                available: *mut u64,
                total: *mut u64,
                free: *mut u64,
            ) -> i32;
        }
        let wide: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let mut available = 0;
        // Buffers remain alive throughout the Win32 call; only available bytes are requested.
        if unsafe {
            GetDiskFreeSpaceExW(
                wide.as_ptr(),
                &mut available,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
            )
        } == 0
        {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(available)
    }
    #[cfg(not(windows))]
    {
        let _ = path;
        Err("Managed installation requires Windows".into())
    }
}

fn safe_relative(s: &str) -> bool {
    !s.is_empty()
        && !s.contains(['\\', ':'])
        && Path::new(s)
            .components()
            .all(|c| matches!(c, std::path::Component::Normal(_)))
}
fn extract(archive: &Path, destination: &Path, cancel: &CancellationToken) -> Result<(), String> {
    let file = fs::File::open(archive).map_err(|e| e.to_string())?;
    let mut archive = zip::ZipArchive::new(file).map_err(|e| e.to_string())?;
    if archive.len() > 20000 {
        return Err("Too many archive entries".into());
    }
    let mut expanded = 0u64;
    for index in 0..archive.len() {
        if cancel.is_cancelled() {
            return Err("Installation cancelled".into());
        }
        let mut entry = archive.by_index(index).map_err(|e| e.to_string())?;
        let name = entry.name().trim_end_matches('/');
        if !safe_relative(name)
            || name.split('/').any(|p| {
                p.ends_with(['.', ' '])
                    || p.split('.').next().is_some_and(|n| {
                        matches!(
                            n.to_ascii_uppercase().as_str(),
                            "CON" | "PRN" | "AUX" | "NUL" | "COM1" | "LPT1"
                        )
                    })
            })
        {
            return Err("Unsafe archive path".into());
        }
        if entry.unix_mode().is_some_and(|m| m & 0o170000 == 0o120000) {
            return Err("Archive links are forbidden".into());
        }
        expanded = expanded
            .checked_add(entry.size())
            .ok_or("Archive size overflow")?;
        if expanded > MAX_EXPANDED {
            return Err("Archive expands beyond the safety limit".into());
        }
        let path = destination.join(name);
        reject_links(&path)?;
        if entry.is_dir() {
            fs::create_dir_all(&path).map_err(|e| e.to_string())?;
            continue;
        }
        fs::create_dir_all(path.parent().ok_or("Invalid archive path")?)
            .map_err(|e| e.to_string())?;
        let mut output = fs::OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&path)
            .map_err(|e| format!("Duplicate or invalid archive entry: {e}"))?;
        let mut copied = 0u64;
        let mut buffer = [0u8; 65536];
        loop {
            if cancel.is_cancelled() {
                return Err("Installation cancelled".into());
            }
            let count = entry.read(&mut buffer).map_err(|e| e.to_string())?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > entry.size() {
                return Err("ZIP entry exceeded its advertised size".into());
            }
            output
                .write_all(&buffer[..count])
                .map_err(|e| e.to_string())?;
        }
        if copied != entry.size() {
            return Err("Truncated ZIP entry".into());
        }
    }
    Ok(())
}
fn inventory(root: &Path) -> Result<Vec<String>, String> {
    fn visit(root: &Path, current: &Path, out: &mut Vec<String>) -> Result<(), String> {
        reject_links(current)?;
        for entry in fs::read_dir(current).map_err(|e| e.to_string())? {
            let path = entry.map_err(|e| e.to_string())?.path();
            reject_links(&path)?;
            if path.is_dir() {
                visit(root, &path, out)?;
            } else {
                out.push(
                    path.strip_prefix(root)
                        .map_err(|e| e.to_string())?
                        .to_string_lossy()
                        .replace('\\', "/"),
                );
            }
        }
        Ok(())
    }
    let mut files = vec![];
    visit(root, root, &mut files)?;
    Ok(files)
}
fn find_server(root: &Path) -> Result<PathBuf, String> {
    let files = inventory(root)?;
    let servers: Vec<_> = files
        .iter()
        .filter(|f| {
            Path::new(f)
                .file_name()
                .is_some_and(|n| n == "llama-server.exe")
        })
        .collect();
    if servers.len() != 1 {
        return Err("Package must contain exactly one llama-server.exe".into());
    }
    Ok(root.join(servers[0]))
}
async fn validate_backend(executable: &Path, backend: &str) -> Result<String, String> {
    if backend == "cpu" {
        return Ok(String::new());
    }
    let mut command = Command::new(executable);
    #[cfg(windows)]
    {
        command.creation_flags(0x08000000);
    }
    command
        .kill_on_drop(true)
        .current_dir(executable.parent().ok_or("Missing server directory")?)
        .arg("--list-devices");
    let output = tokio::time::timeout(Duration::from_secs(45), command.output())
        .await
        .map_err(|_| "Hardware device probe timed out")?
        .map_err(|e| e.to_string())?;
    let text = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let prefixes: &[&str] = match backend {
        "sycl" => &["SYCL"],
        "cuda12" | "cuda13" => &["CUDA"],
        "vulkan" => &["Vulkan"],
        "rocm" => &["ROCm", "HIP"],
        "openvino" => &["OpenVINO"],
        _ => return Err("Unknown backend".into()),
    };
    if !output.status.success() || !has_backend_device(&text, prefixes) {
        return Err(format!("此 {backend} 套件未辨識到可用裝置；請確認相容 GPU、驅動與套件執行期。原有版本會保留。\n{}", text.chars().take(1600).collect::<String>()));
    }
    Ok(text.chars().take(4000).collect())
}

fn has_backend_device(text: &str, prefixes: &[&str]) -> bool {
    text.lines().any(|line| {
        let Some((id, description)) = line.trim().split_once(':') else {
            return false;
        };
        !description.trim().is_empty()
            && prefixes.iter().any(|prefix| {
                id.get(..prefix.len())
                    .is_some_and(|name| name.eq_ignore_ascii_case(prefix))
                    && id.get(prefix.len()..).is_some_and(|index| {
                        !index.is_empty() && index.bytes().all(|c| c.is_ascii_digit())
                    })
            })
    })
}

async fn probe(executable: &Path) -> Result<String, String> {
    let mut command = Command::new(executable);
    #[cfg(windows)]
    {
        command.creation_flags(0x08000000);
    }
    command
        .kill_on_drop(true)
        .current_dir(executable.parent().ok_or("Missing server directory")?)
        .arg("--version");
    let output = tokio::time::timeout(Duration::from_secs(30), command.output())
        .await
        .map_err(|_| "Engine probe timed out")?
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(
            "Engine binary cannot start on this machine; previous version remains selected".into(),
        );
    }
    Ok(format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    )
    .chars()
    .take(4000)
    .collect())
}

fn hash_file(path: &Path) -> Result<String, String> {
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let count = file.read(&mut buffer).map_err(|e| e.to_string())?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn removing_last_selected_package_clears_selection_and_disables_updates() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path().join("engines");
        let payload = root.join("llama.cpp/versions/test-cpu");
        fs::create_dir_all(&payload).unwrap();
        fs::write(payload.join("llama-server.exe"), b"test fixture").unwrap();
        let package = Package {
            id: "test-cpu".into(),
            engine_id: "llama.cpp".into(),
            channel: "stable".into(),
            version: "test".into(),
            build_tag: "test".into(),
            backend: "cpu".into(),
            assets: vec![],
            installed_at: None,
            files: vec!["llama-server.exe".into()],
            probe: String::new(),
            hashes: [(
                "llama-server.exe".into(),
                hash_file(&payload.join("llama-server.exe")).unwrap(),
            )]
            .into(),
        };
        let manifest = Manifest {
            active: Some(package.id.clone()),
            previous: Some(package.id.clone()),
            pending: Some(package.id.clone()),
            packages: vec![package],
            policy: Policy {
                mode: "managed".into(),
                update: "auto".into(),
                ..Policy::default()
            },
            ..Manifest::default()
        };
        fs::write(
            root.join("manifest.json"),
            serde_json::to_vec(&manifest).unwrap(),
        )
        .unwrap();
        let manager = EngineManager::open(dir.path()).unwrap();
        let status = manager.remove("test-cpu").await.unwrap();
        assert!(status["active"].is_null());
        assert!(status["previous"].is_null());
        assert!(status["pending"].is_null());
        assert_eq!(status["packages"], json!([]));
        assert_eq!(status["policy"]["update"], "off");
        assert!(!payload.exists());
        assert!(manager.active_directory().await.unwrap().is_none());
    }
    #[test]
    fn device_probe_accepts_openvino_cpu_and_gpu_output() {
        let output = "Available devices:\n  OPENVINO0: GGML_OPENVINO_DEVICE=CPU (selected) - 12th Gen Intel(R) Core(TM) i5-12500H (16076 MiB, 7834 MiB free)\n  OPENVINO1: GGML_OPENVINO_DEVICE=GPU - Intel(R) Iris(R) Xe Graphics (iGPU) (7105 MiB, 7105 MiB free)\n";
        assert!(has_backend_device(output, &["OpenVINO"]));
        assert!(has_backend_device("OpenVINO1: Intel GPU", &["OpenVINO"]));
        for (line, prefix) in [
            ("SYCL0: Intel GPU", "SYCL"),
            ("CUDA0: NVIDIA GPU", "CUDA"),
            ("Vulkan0: GPU", "Vulkan"),
            ("ROCm0: AMD GPU", "ROCm"),
            ("HIP0: AMD GPU", "HIP"),
        ] {
            assert!(has_backend_device(line, &[prefix]));
        }
        for output in [
            "Available devices:\n",
            "OpenVINO: no devices found",
            "OPENVINO error: initialization failed",
            "OPENVINO0:",
        ] {
            assert!(!has_backend_device(output, &["OpenVINO"]));
        }
        assert!(!has_backend_device("CUDA0: NVIDIA GPU", &["OpenVINO"]));
    }
    #[test]
    fn pascal_never_recommends_cuda13_even_with_new_driver() {
        let mut h = json!({"gpu":[{"Name":"NVIDIA GeForce GTX 1070 Ti"}],"nvidia_devices":[{"name":"GTX 1070 Ti","compute_capability":6.1}],"cuda_driver_max":"13.4"});
        recommend(&mut h);
        assert_eq!(h["recommendation"], "cuda12");
        assert_eq!(h["backends"][2]["eligible"], false);
        h["nvidia_devices"][0]["compute_capability"] = json!(12.0);
        recommend(&mut h);
        assert_eq!(h["recommendation"], "cuda13");
        assert_eq!(h["backends"][1]["eligible"], false);
    }
    #[test]
    fn iris_xe_gets_sycl_and_unlisted_amd_is_not_rocm_certified() {
        let mut h = json!({"cpu":[{"Name":"12th Gen Intel(R) Core(TM) i5-12500H"}],"gpu":[{"Name":"Intel(R) Iris(R) Xe Graphics"}]});
        recommend(&mut h);
        assert_eq!(h["recommendation"], "sycl");
        assert_eq!(h["backends"][4]["eligible"], true);
        h["gpu"] = json!([{"Name":"AMD Radeon RX 580"}]);
        recommend(&mut h);
        assert_eq!(h["backends"][5]["eligible"], false);
        assert_eq!(h["backends"][6]["eligible"], true);
    }
    #[test]
    fn rocm_recommendation_requires_windows_and_driver_not_just_radeon_brand() {
        let mut h = json!({"gpu":[{"Name":"AMD Radeon RX 7900 XTX"}],"windows_build":"26200","amd_software_version":"26.8.1"});
        recommend(&mut h);
        assert_eq!(h["recommendation"], "rocm");
        h["windows_build"] = json!("22631");
        recommend(&mut h);
        assert_eq!(h["recommendation"], "vulkan");
        h["windows_build"] = json!("26200");
        h["amd_software_version"] = Value::Null;
        recommend(&mut h);
        assert_eq!(h["recommendation"], "vulkan");
    }
    #[tokio::test]
    async fn english_directory_migrates_existing_manifest_and_preserves_unknown_files() {
        let dir = tempfile::tempdir().unwrap();
        let legacy = dir.path().join("推理引擎");
        fs::create_dir(&legacy).unwrap();
        fs::write(
            legacy.join("manifest.json"),
            serde_json::to_vec(&Manifest::default()).unwrap(),
        )
        .unwrap();
        fs::write(legacy.join("user-note.txt"), "keep").unwrap();
        let manager = EngineManager::open(dir.path()).unwrap();
        assert_eq!(
            manager.status().await["root"],
            json!(dir.path().join("engines"))
        );
        assert!(!legacy.exists());
        assert_eq!(
            fs::read_to_string(dir.path().join("engines/user-note.txt")).unwrap(),
            "keep"
        );
    }
    #[test]
    fn rejects_escape_and_unsafe_policy() {
        for path in ["../x", "/x", "C:/x", "a\\b", "a/../b"] {
            assert!(!safe_relative(path));
        }
        assert!(!valid_id("../engine"));
        let p = Policy {
            channel: "latest".into(),
            ..Policy::default()
        };
        assert!(p.validate().is_err());
    }
    #[test]
    fn stable_identity_retains_mapped_build() {
        let release = json!({"tag_name":"b123","assets":[{"name":"llama-b123-bin-win-cpu-x64.zip","browser_download_url":"https://github.com/ggml-org/llama.cpp/releases/download/b123/llama-b123-bin-win-cpu-x64.zip","digest":format!("sha256:{}","a".repeat(64)),"size":123}]});
        let p = package_from_release(&release, "stable", "v0.6.0", "cpu").unwrap();
        assert_eq!(p.version, "v0.6.0");
        assert_eq!(p.build_tag, "b123");
        assert_ne!(
            p.id,
            package_from_release(&release, "preview", "b123", "cpu")
                .unwrap()
                .id
        );
        let mut invalid = release;
        invalid["assets"][0]["digest"] = Value::Null;
        assert!(package_from_release(&invalid, "stable", "v0.6.0", "cpu").is_err());
    }
    #[test]
    fn corrupt_archive_cannot_publish() {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("bad.zip");
        fs::write(&archive, b"not a zip").unwrap();
        assert!(extract(&archive, dir.path(), &CancellationToken::new()).is_err());
    }
    #[test]
    fn archive_escape_is_rejected_before_writing() {
        let dir = tempfile::tempdir().unwrap();
        let archive = dir.path().join("escape.zip");
        let mut zip = zip::ZipWriter::new(fs::File::create(&archive).unwrap());
        zip.start_file("../outside.txt", zip::write::SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"outside").unwrap();
        zip.finish().unwrap();
        let payload = dir.path().join("payload");
        fs::create_dir(&payload).unwrap();
        assert!(extract(&archive, &payload, &CancellationToken::new()).is_err());
        assert!(!dir.path().join("outside.txt").exists());
    }
    #[tokio::test]
    async fn multiple_managers_cannot_overwrite_each_others_policy() {
        let dir = tempfile::tempdir().unwrap();
        let first = EngineManager::open(dir.path()).unwrap();
        let second = EngineManager::open(dir.path()).unwrap();
        let policy = Policy {
            channel: "preview".into(),
            ..Policy::default()
        };
        first.policy(policy.clone()).await.unwrap();
        assert!(second.policy(Policy::default()).await.is_err());
        assert_eq!(first.status().await["policy"]["channel"], "preview");
    }
}
