use std::path::{Path, PathBuf};
use std::io::{Read, Write};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};

struct BackendChild(Mutex<Option<Child>>);

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

fn main() {
    tauri::Builder::default()
        .setup(|app| {
            setup_backend(&app.handle())?;
            Ok(())
        })
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
