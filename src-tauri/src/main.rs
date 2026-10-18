use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use semver::Version;
use tauri::{AppHandle, Manager, RunEvent};
#[cfg(not(any(target_os = "android", target_os = "ios")))]
use tauri_plugin_updater::UpdaterExt;
use url::Url;

struct BackendChild(Mutex<Option<Child>>);
struct DesktopUpdateRuntime(Mutex<Option<String>>);

#[derive(Clone, Debug)]
struct DesktopUpdateConfig {
    channel: String,
    endpoint: Option<String>,
    pubkey: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ChannelManifestPlatform {
    url: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
struct ChannelManifest {
    version: Option<String>,
    notes: Option<String>,
    #[serde(alias = "pub_date")]
    pub_date: Option<String>,
    platforms: Option<std::collections::HashMap<String, ChannelManifestPlatform>>,
}

#[derive(Clone, Debug, Default, Deserialize)]
struct PersistedDesktopUpdateConfig {
    channel: Option<String>,
    endpoint: Option<String>,
    pubkey: Option<String>,
}

#[derive(Serialize)]
struct DesktopUpdateStatus {
    desktop: bool,
    configured: bool,
    channel: String,
    endpoint: Option<String>,
    current_version: String,
    update_available: bool,
    latest_version: Option<String>,
    installed_version: Option<String>,
    restart_required: bool,
    notes: Option<String>,
    published_at: Option<String>,
    download_url: Option<String>,
    error: Option<String>,
}

#[derive(Clone, Debug, Default)]
struct ManualManifestCheck {
    version: Option<String>,
    notes: Option<String>,
    published_at: Option<String>,
    download_url: Option<String>,
    update_available: bool,
    error: Option<String>,
}

#[derive(Serialize)]
struct DesktopInstallResult {
    installed: bool,
    version: Option<String>,
}

fn resolve_project_root() -> Result<PathBuf, String> {
    let cwd = std::env::current_dir()
        .map_err(|err| format!("Failed to resolve current working directory: {err}"))?;
    if cwd.join("strata").is_dir() && cwd.join("strata_ui").is_dir() {
        return Ok(cwd);
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let candidate = manifest_dir
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "Failed to resolve repository root from Cargo manifest directory.".to_string())?;

    if candidate.join("strata").is_dir() && candidate.join("strata_ui").is_dir() {
        return Ok(candidate);
    }

    Err(format!(
        "Unable to locate the Strata project root from cwd {:?} or manifest dir {:?}.",
        cwd, manifest_dir
    ))
}

fn candidate_python_paths(root_dir: &Path) -> Vec<PathBuf> {
    vec![
        root_dir.join("venv").join("bin").join("python"),
        root_dir.join("venv_new").join("bin").join("python"),
        PathBuf::from("python3"),
        PathBuf::from("python"),
    ]
}

fn localhost_port_open(port: u16) -> bool {
    std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("valid localhost socket address"),
        Duration::from_millis(500),
    )
    .is_ok()
}

fn backend_health_ok(port: u16) -> bool {
    let mut stream = match std::net::TcpStream::connect_timeout(
        &format!("127.0.0.1:{port}")
            .parse()
            .expect("valid localhost socket address"),
        Duration::from_millis(750),
    ) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_millis(750)));
    let _ = stream.set_write_timeout(Some(Duration::from_millis(750)));

    let request = b"GET /admin/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}

fn wait_for_backend(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if localhost_port_open(port) && backend_health_ok(port) {
            return true;
        }
        thread::sleep(Duration::from_millis(1000));
    }
    false
}

fn find_python(root_dir: &Path) -> Option<PathBuf> {
    candidate_python_paths(root_dir)
        .into_iter()
        .find(|path| path.is_absolute() || path.exists())
}

fn start_backend(root_dir: &Path) -> Result<Option<Child>, String> {
    if localhost_port_open(8000) && backend_health_ok(8000) {
        return Ok(None);
    }

    let python = find_python(root_dir).ok_or_else(|| "No Python runtime found for Strata backend startup.".to_string())?;
    let runtime_dir = root_dir.join("strata").join("runtime");
    std::fs::create_dir_all(&runtime_dir).map_err(|err| format!("Failed to create runtime dir: {err}"))?;

    let log_path = runtime_dir.join("desktop-api.log");
    let stdout = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .map_err(|err| format!("Failed to open backend log file: {err}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|err| format!("Failed to clone backend log file handle: {err}"))?;

    let start_script = root_dir.join("scripts").join("start_api.sh");
    let child = if start_script.exists() {
        Command::new("bash")
            .current_dir(root_dir)
            .arg(start_script)
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr))
            .spawn()
            .map_err(|err| format!("Failed to launch Strata backend via start_api.sh: {err}"))?
    } else {
        Command::new(&python)
            .current_dir(root_dir)
            .env("PYTHONPATH", ".")
            .arg("-m")
            .arg("uvicorn")
            .arg("strata.api.main:app")
            .arg("--host")
            .arg("127.0.0.1")
            .arg("--port")
            .arg("8000")
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr))
            .spawn()
            .map_err(|err| format!("Failed to launch Strata backend with {:?}: {err}", python))?
    };

    if !wait_for_backend(8000, Duration::from_secs(90)) {
        return Err(format!(
            "Strata backend did not become healthy on http://127.0.0.1:8000 within 90 seconds. Check {:?} for startup logs.",
            log_path
        ));
    }

    Ok(Some(child))
}

fn setup_backend(app: &AppHandle) -> Result<(), String> {
    let root_dir = resolve_project_root()?;

    let child = start_backend(&root_dir)?;
    app.manage(BackendChild(Mutex::new(child)));
    Ok(())
}

fn kill_managed_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut child_guard) = state.0.lock() {
            if let Some(child) = child_guard.as_mut() {
                let _ = child.kill();
                let _ = child.wait();
            }
            *child_guard = None;
        }
    }
}

fn persisted_desktop_update_config() -> Option<PersistedDesktopUpdateConfig> {
    let root_dir = resolve_project_root().ok()?;
    let config_path = root_dir.join("strata").join("runtime").join("desktop-updater.json");
    let raw = std::fs::read_to_string(config_path).ok()?;
    serde_json::from_str::<PersistedDesktopUpdateConfig>(&raw).ok()
}

fn desktop_update_config() -> DesktopUpdateConfig {
    let persisted = persisted_desktop_update_config().unwrap_or_default();
    let channel = std::env::var("STRATA_DESKTOP_UPDATE_CHANNEL")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .or_else(|| persisted.channel.clone().filter(|value| !value.trim().is_empty()))
        .unwrap_or_else(|| "alpha".to_string());

    let endpoint = std::env::var("STRATA_DESKTOP_UPDATE_ENDPOINT")
        .ok()
        .map(|value| value.trim().replace("{channel}", &channel))
        .filter(|value| !value.is_empty())
        .or_else(|| {
            persisted.endpoint.as_ref().map(|value| value.trim().replace("{channel}", &channel))
        })
        .filter(|value| !value.is_empty());

    let pubkey = std::env::var("STRATA_DESKTOP_UPDATE_PUBKEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .or_else(|| persisted.pubkey.clone().filter(|value| !value.trim().is_empty()));

    DesktopUpdateConfig {
        channel,
        endpoint,
        pubkey,
    }
}

fn version_is_newer(candidate: &str, current: &str) -> bool {
    let candidate = candidate.trim();
    let current = current.trim();
    match (Version::parse(candidate), Version::parse(current)) {
        (Ok(candidate_version), Ok(current_version)) => candidate_version > current_version,
        _ => false,
    }
}

fn fetch_channel_manifest(endpoint: &str) -> Result<ChannelManifest, String> {
    let url = Url::parse(endpoint).map_err(|err| format!("Invalid desktop updater endpoint: {err}"))?;
    if url.scheme() != "http" {
        return Err("Manual desktop update manifest fallback currently supports only http endpoints.".to_string());
    }
    let host = url
        .host_str()
        .ok_or_else(|| "Desktop updater endpoint is missing a host.".to_string())?;
    let port = url.port_or_known_default().unwrap_or(80);
    let mut path = url.path().to_string();
    if path.is_empty() {
        path = "/".to_string();
    }
    if let Some(query) = url.query() {
        path.push('?');
        path.push_str(query);
    }

    let mut stream = std::net::TcpStream::connect((host, port))
        .map_err(|err| format!("Failed to connect to desktop updater endpoint: {err}"))?;
    let _ = stream.set_read_timeout(Some(Duration::from_secs(5)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(5)));

    let request = format!(
        "GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\nAccept: application/json\r\n\r\n"
    );
    stream
        .write_all(request.as_bytes())
        .map_err(|err| format!("Failed to request desktop update manifest: {err}"))?;

    let mut response = Vec::new();
    stream
        .read_to_end(&mut response)
        .map_err(|err| format!("Failed to read desktop update manifest: {err}"))?;
    let response_text = String::from_utf8_lossy(&response);
    let mut parts = response_text.splitn(2, "\r\n\r\n");
    let headers = parts.next().unwrap_or_default();
    let body = parts.next().unwrap_or_default();
    if !headers.starts_with("HTTP/1.1 200") && !headers.starts_with("HTTP/1.0 200") {
        return Err(format!("Desktop updater endpoint returned a non-200 response: {}", headers.lines().next().unwrap_or_default()));
    }
    serde_json::from_str::<ChannelManifest>(body)
        .map_err(|err| format!("Failed to parse desktop update manifest: {err}"))
}

fn manual_manifest_check(config: &DesktopUpdateConfig, current_version: &str) -> ManualManifestCheck {
    let Some(endpoint) = config.endpoint.as_deref() else {
        return ManualManifestCheck {
            error: Some("Desktop updater endpoint is not configured.".to_string()),
            ..ManualManifestCheck::default()
        };
    };

    match fetch_channel_manifest(endpoint) {
        Ok(manifest) => {
            let version = manifest.version.as_ref().map(|value| value.trim().to_string());
            let manifest_version = version.clone().unwrap_or_default();
            let update_available = version_is_newer(&manifest_version, current_version);
            let download_url = manifest.platforms.as_ref().and_then(|platforms| {
                platforms.values().find_map(|platform| platform.url.clone())
            });

            ManualManifestCheck {
                version,
                notes: manifest.notes,
                published_at: manifest.pub_date,
                download_url,
                update_available,
                error: None,
            }
        }
        Err(err) => ManualManifestCheck {
            error: Some(err),
            ..ManualManifestCheck::default()
        },
    }
}

#[cfg(not(any(target_os = "android", target_os = "ios")))]
fn configured_updater(
    app: &AppHandle,
    config: &DesktopUpdateConfig,
) -> Result<tauri_plugin_updater::Updater, String> {
    let endpoint = config
        .endpoint
        .as_ref()
        .ok_or_else(|| "Desktop updater endpoint is not configured.".to_string())?;
    let pubkey = config
        .pubkey
        .as_ref()
        .ok_or_else(|| "Desktop updater public key is not configured.".to_string())?;

    let endpoint_url = Url::parse(endpoint).map_err(|err| format!("Invalid desktop updater endpoint: {err}"))?;
    app.updater_builder()
        .pubkey(pubkey.clone())
        .endpoints(vec![endpoint_url])
        .map_err(|err| format!("Failed to configure desktop updater endpoint: {err}"))?
        .build()
        .map_err(|err| format!("Failed to build desktop updater: {err}"))
}

#[cfg(not(any(target_os = "android", target_os = "ios")))]
fn desktop_builder() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default().plugin(tauri_plugin_updater::Builder::new().build())
}

#[cfg(any(target_os = "android", target_os = "ios"))]
fn desktop_builder() -> tauri::Builder<tauri::Wry> {
    tauri::Builder::default()
}

#[tauri::command]
async fn desktop_update_status(app: AppHandle) -> Result<DesktopUpdateStatus, String> {
    let config = desktop_update_config();
    let current_version = app.package_info().version.to_string();
    let manual_manifest = manual_manifest_check(&config, &current_version);
    let installed_version = app
        .try_state::<DesktopUpdateRuntime>()
        .and_then(|state| state.0.lock().ok().and_then(|pending| pending.clone()));
    let restart_required = installed_version.is_some();

    #[cfg(any(target_os = "android", target_os = "ios"))]
    {
        return Ok(DesktopUpdateStatus {
            desktop: false,
            configured: false,
            channel: config.channel,
            endpoint: config.endpoint,
            current_version,
            update_available: false,
            latest_version: None,
            installed_version,
            restart_required,
            notes: None,
            published_at: None,
            download_url: None,
            error: Some("Desktop updater is not supported on this target.".to_string()),
        });
    }

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        let configured = config.endpoint.is_some() && config.pubkey.is_some();
        if !configured {
            return Ok(DesktopUpdateStatus {
                desktop: true,
                configured: false,
                channel: config.channel,
                endpoint: config.endpoint,
                current_version,
                update_available: false,
                latest_version: None,
                installed_version,
                restart_required,
                notes: None,
                published_at: None,
                download_url: None,
                error: Some("Desktop updater is not configured yet. Set STRATA_DESKTOP_UPDATE_ENDPOINT and STRATA_DESKTOP_UPDATE_PUBKEY.".to_string()),
            });
        }

        let updater = match configured_updater(&app, &config) {
            Ok(updater) => updater,
            Err(err) => {
                return Ok(DesktopUpdateStatus {
                    desktop: true,
                    configured: true,
                    channel: config.channel,
                    endpoint: config.endpoint,
                    current_version,
                    update_available: false,
                    latest_version: None,
                    installed_version,
                    restart_required,
                    notes: None,
                    published_at: None,
                    download_url: None,
                    error: Some(match manual_manifest.error {
                        Some(manifest_err) => format!("{err} Manual manifest check also failed: {manifest_err}"),
                        None => err,
                    }),
                });
            }
        };
        let update = match updater.check().await {
            Ok(update) => update,
            Err(err) => {
                if manual_manifest.update_available {
                    return Ok(DesktopUpdateStatus {
                        desktop: true,
                        configured: true,
                        channel: config.channel,
                        endpoint: config.endpoint,
                        current_version,
                        update_available: true,
                        latest_version: manual_manifest.version,
                        installed_version,
                        restart_required,
                        notes: manual_manifest.notes,
                        published_at: manual_manifest.published_at,
                        download_url: manual_manifest.download_url,
                        error: Some(format!("Updater plugin check failed, but local channel manifest shows a newer version: {err}")),
                    });
                }

                return Ok(DesktopUpdateStatus {
                    desktop: true,
                    configured: true,
                    channel: config.channel,
                    endpoint: config.endpoint,
                    current_version,
                    update_available: false,
                    latest_version: manual_manifest.version,
                    installed_version,
                    restart_required,
                    notes: manual_manifest.notes,
                    published_at: manual_manifest.published_at,
                    download_url: manual_manifest.download_url,
                    error: Some(match manual_manifest.error {
                        Some(manifest_err) => format!("Failed to check for desktop updates: {err}. Manual manifest check also failed: {manifest_err}"),
                        None => format!("Failed to check for desktop updates: {err}"),
                    }),
                });
            }
        };

        if let Some(update) = update {
            return Ok(DesktopUpdateStatus {
                desktop: true,
                configured: true,
                channel: config.channel,
                endpoint: config.endpoint,
                current_version,
                update_available: true,
                latest_version: Some(update.version.clone()),
                installed_version,
                restart_required,
                notes: update.body.clone(),
                published_at: update.date.map(|date| date.to_string()),
                download_url: Some(update.download_url.to_string()),
                error: None,
            });
        }

        if manual_manifest.update_available {
            return Ok(DesktopUpdateStatus {
                desktop: true,
                configured: true,
                channel: config.channel,
                endpoint: config.endpoint,
                current_version,
                update_available: true,
                latest_version: manual_manifest.version,
                installed_version,
                restart_required,
                notes: manual_manifest.notes,
                published_at: manual_manifest.published_at,
                download_url: manual_manifest.download_url,
                error: None,
            });
        }

        Ok(DesktopUpdateStatus {
            desktop: true,
            configured: true,
            channel: config.channel,
            endpoint: config.endpoint,
            current_version,
            update_available: false,
            latest_version: manual_manifest.version,
            installed_version,
            restart_required,
            notes: manual_manifest.notes,
            published_at: manual_manifest.published_at,
            download_url: manual_manifest.download_url,
            error: manual_manifest.error,
        })
    }
}

#[tauri::command]
async fn desktop_install_update(app: AppHandle) -> Result<DesktopInstallResult, String> {
    #[cfg(any(target_os = "android", target_os = "ios"))]
    {
        let _ = app;
        return Ok(DesktopInstallResult {
            installed: false,
            version: None,
        });
    }

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        let config = desktop_update_config();
        let updater = configured_updater(&app, &config)?;
        let update = updater
            .check()
            .await
            .map_err(|err| format!("Failed to check for desktop updates: {err}"))?;

        let Some(update) = update else {
            return Ok(DesktopInstallResult {
                installed: false,
                version: None,
            });
        };

        let version = update.version.clone();
        update
            .download_and_install(|_, _| {}, || {})
            .await
            .map_err(|err| format!("Failed to install desktop update: {err}"))?;

        if let Some(state) = app.try_state::<DesktopUpdateRuntime>() {
            if let Ok(mut pending) = state.0.lock() {
                *pending = Some(version.clone());
            }
        }

        Ok(DesktopInstallResult {
            installed: true,
            version: Some(version),
        })
    }
}

#[tauri::command]
async fn desktop_restart(app: AppHandle) -> Result<(), String> {
    #[cfg(any(target_os = "android", target_os = "ios"))]
    {
        let _ = app;
        return Err("Desktop restart is not supported on this target.".to_string());
    }

    #[cfg(not(any(target_os = "android", target_os = "ios")))]
    {
        app.request_restart();
        Ok(())
    }
}

#[tauri::command]
async fn desktop_reconnect_backend(app: AppHandle) -> Result<bool, String> {
    let root_dir = resolve_project_root()?;

    if localhost_port_open(8000) && backend_health_ok(8000) {
        return Ok(true);
    }

    kill_managed_backend(&app);

    let child = start_backend(&root_dir)?;
    if let Some(state) = app.try_state::<BackendChild>() {
        if let Ok(mut child_guard) = state.0.lock() {
            *child_guard = child;
        }
    }

    Ok(localhost_port_open(8000) && backend_health_ok(8000))
}

fn main() {
    desktop_builder()
        .manage(DesktopUpdateRuntime(Mutex::new(None)))
        .setup(|app| {
            setup_backend(&app.handle())?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            desktop_update_status,
            desktop_install_update,
            desktop_restart,
            desktop_reconnect_backend
        ])
        .build(tauri::generate_context!())
        .expect("error while building Strata desktop shell")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                kill_managed_backend(&app_handle);
            }
        });
}
