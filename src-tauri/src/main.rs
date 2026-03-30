use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use serde::Serialize;
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

fn desktop_update_config() -> DesktopUpdateConfig {
    let channel = std::env::var("STRATA_DESKTOP_UPDATE_CHANNEL")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "alpha".to_string());

    let endpoint = std::env::var("STRATA_DESKTOP_UPDATE_ENDPOINT")
        .ok()
        .map(|value| value.trim().replace("{channel}", &channel))
        .filter(|value| !value.is_empty());

    let pubkey = std::env::var("STRATA_DESKTOP_UPDATE_PUBKEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty());

    DesktopUpdateConfig {
        channel,
        endpoint,
        pubkey,
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

        let updater = configured_updater(&app, &config)?;
        let update = updater
            .check()
            .await
            .map_err(|err| format!("Failed to check for desktop updates: {err}"))?;

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

        Ok(DesktopUpdateStatus {
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
            error: None,
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
            desktop_restart
        ])
        .build(tauri::generate_context!())
        .expect("error while building Strata desktop shell")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<BackendChild>() {
                    if let Ok(mut child_guard) = state.0.lock() {
                        if let Some(child) = child_guard.as_mut() {
                            let _ = child.kill();
                        }
                    }
                }
            }
        });
}
