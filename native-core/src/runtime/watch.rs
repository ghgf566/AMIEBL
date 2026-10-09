//! Original VS Code log tail rules: initial history is skipped, rotation is
//! replayed, and only the active request may be cancelled.
use super::*;
use std::io::{Read, Seek, SeekFrom};

fn discover(root: &Path) -> Option<PathBuf> {
    fn visit(root: &Path, candidates: &mut Vec<(std::time::SystemTime, PathBuf)>) {
        let Ok(entries) = std::fs::read_dir(root) else {
            return;
        };
        for entry in entries.flatten() {
            let path = entry.path();
            let Ok(kind) = entry.file_type() else {
                continue;
            };
            if kind.is_dir() {
                visit(&path, candidates);
            } else if path.extension().is_some_and(|e| e == "log")
                && (path
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .to_lowercase()
                    .contains("agent")
                    || path.to_string_lossy().to_lowercase().contains("copilot"))
            {
                let Ok(meta) = path.metadata() else { continue };
                let Ok(modified) = meta.modified() else {
                    continue;
                };
                if meta.len() < 64 * 1024 * 1024
                    && modified.elapsed().unwrap_or_default().as_secs() < 21600
                {
                    candidates.push((modified, path));
                }
            }
        }
    }
    let mut candidates = Vec::new();
    visit(root, &mut candidates);
    candidates.into_iter().max().map(|(_, p)| p)
}

impl Manager {
    pub(super) async fn watch_vscode(self: Arc<Self>) {
        let root = std::env::var_os("VSCODE_LOG_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("APPDATA").unwrap_or_default()).join("Code/logs")
            });
        let mut watched = None;
        let mut offset = 0;
        let mut partial = String::new();
        let mut initial = true;
        let mut discovery_at = Instant::now();
        let timestamp =
            regex::Regex::new(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)").unwrap();
        while !self.stopping.is_cancelled() {
            let enabled = self.inner.lock().await.store.config()["vscode_abort_watch"] != false
                && std::env::var("VSCODE_ABORT_LOG_WATCH").as_deref() != Ok("0");
            if enabled {
                if Instant::now() >= discovery_at {
                    let root = root.clone();
                    let candidate = tokio::task::spawn_blocking(move || discover(&root))
                        .await
                        .ok()
                        .flatten();
                    discovery_at = Instant::now() + Duration::from_secs(5);
                    if candidate != watched {
                        watched = candidate;
                        partial.clear();
                        offset = watched
                            .as_ref()
                            .and_then(|p| p.metadata().ok())
                            .map(|m| {
                                if initial {
                                    m.len()
                                } else {
                                    m.len().saturating_sub(16 * 1024)
                                }
                            })
                            .unwrap_or(0);
                    }
                    initial = false;
                }
                if let Some(path) = &watched {
                    let read = (|| -> std::io::Result<Vec<u8>> {
                        let size = path.metadata()?.len();
                        if size < offset {
                            offset = size.saturating_sub(16 * 1024);
                            partial.clear();
                        }
                        let mut file = std::fs::File::open(path)?;
                        file.seek(SeekFrom::Start(offset))?;
                        let mut raw = Vec::new();
                        file.take(64 * 1024).read_to_end(&mut raw)?;
                        offset += raw.len() as u64;
                        Ok(raw)
                    })();
                    match read {
                        Err(_) => watched = None,
                        Ok(raw) => {
                            partial.push_str(&String::from_utf8_lossy(&raw));
                            while let Some(end) = partial.find(['\n', '\r']) {
                                let line = partial.drain(..=end).collect::<String>();
                                let low = line.to_lowercase();
                                let Some(phrase) = [
                                    "aborting session",
                                    "cancelling session",
                                    "canceling session",
                                    "session aborted",
                                    "abort session",
                                ]
                                .into_iter()
                                .find(|p| low.contains(p)) else {
                                    continue;
                                };
                                let ticket = {
                                    let i = self.inner.lock().await;
                                    i.active.as_ref().and_then(|id| i.tickets.get(id)).cloned()
                                };
                                let Some(ticket) = ticket
                                    .filter(|t| !t.done.is_cancelled() && !t.cancel.is_cancelled())
                                else {
                                    continue;
                                };
                                if let Some(event) = timestamp.captures(&line).and_then(|m| {
                                    chrono::NaiveDateTime::parse_from_str(
                                        &m[1],
                                        "%Y-%m-%d %H:%M:%S%.f",
                                    )
                                    .ok()
                                }) {
                                    let started = ticket.record.lock().unwrap()["started_at"]
                                        .as_str()
                                        .and_then(|s| chrono::DateTime::parse_from_rfc3339(s).ok())
                                        .map(|t| t.with_timezone(&chrono::Local).naive_local());
                                    if started.is_some_and(|s| event < s)
                                        || chrono::Local::now()
                                            .naive_local()
                                            .signed_duration_since(event)
                                            > chrono::Duration::seconds(10)
                                    {
                                        continue;
                                    }
                                }
                                self.log(&format!(
                                    "偵測到 VS Code 停止訊號 ({phrase})，取消目前執行中的任務。"
                                ))
                                .await;
                                self.cancel(ticket).await;
                            }
                        }
                    }
                }
            }
            sleep(Duration::from_millis(300)).await;
        }
    }

    pub(super) async fn sample_resources(&self) -> Value {
        let mut result = self.inner.lock().await.resources.clone();
        #[cfg(windows)]
        {
            #[repr(C)]
            struct Memory {
                length: u32,
                load: u32,
                total: u64,
                available: u64,
                page_total: u64,
                page_available: u64,
                virtual_total: u64,
                virtual_available: u64,
                extended: u64,
            }
            #[link(name = "kernel32")]
            unsafe extern "system" {
                fn GlobalMemoryStatusEx(memory: *mut Memory) -> i32;
            }
            let mut memory = Memory {
                length: std::mem::size_of::<Memory>() as u32,
                load: 0,
                total: 0,
                available: 0,
                page_total: 0,
                page_available: 0,
                virtual_total: 0,
                virtual_available: 0,
                extended: 0,
            };
            // The structure matches MEMORYSTATUSEX and remains live for this call.
            if unsafe { GlobalMemoryStatusEx(&mut memory) } != 0 {
                result["ram_total_gb"] = json!(round(memory.total as f64 / 1024f64.powi(3), 2));
                result["ram_used_gb"] = json!(round(
                    (memory.total - memory.available) as f64 / 1024f64.powi(3),
                    2
                ));
            }
        }
        let output = timeout(
            Duration::from_secs(2),
            hidden_command("nvidia-smi")
                .args([
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ])
                .output(),
        )
        .await;
        if let Ok(Ok(output)) = output {
            if output.status.success() {
                let pairs = String::from_utf8_lossy(&output.stdout)
                    .lines()
                    .map(|line| {
                        line.split(',')
                            .map(|n| n.trim().parse::<f64>())
                            .collect::<Result<Vec<_>, _>>()
                    })
                    .collect::<Result<Vec<_>, _>>();
                if let Some(pairs) = pairs.ok().filter(|rows| {
                    rows.iter()
                        .all(|row| row.len() >= 2 && row.iter().all(|n| n.is_finite()))
                }) {
                    result["gpu_used_mib"] = json!(pairs.iter().map(|p| p[0]).sum::<f64>());
                    result["gpu_total_mib"] = json!(pairs.iter().map(|p| p[1]).sum::<f64>());
                } else {
                    result["gpu_used_mib"] = Value::Null;
                    result["gpu_total_mib"] = Value::Null;
                }
            }
        }
        result
    }
}
