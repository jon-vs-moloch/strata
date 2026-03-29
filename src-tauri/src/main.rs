use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, RunEvent};

struct BackendChild(Mutex<Option<Child>>);

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

fn wait_for_port(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if localhost_port_open(port) {
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
    if localhost_port_open(8000) {
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

    let child = Command::new(&python)
        .current_dir(root_dir)
        .env("PYTHONPATH", ".")
        .arg("-m")
        .arg("uvicorn")
        .arg("strata.api.main:app")
        .arg("--host")
        .arg("0.0.0.0")
        .arg("--port")
        .arg("8000")
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr))
        .spawn()
        .map_err(|err| format!("Failed to launch Strata backend with {:?}: {err}", python))?;

    if !wait_for_port(8000, Duration::from_secs(90)) {
        return Err("Strata backend did not become reachable on port 8000 within 90 seconds.".to_string());
    }

    Ok(Some(child))
}

fn setup_backend(app: &AppHandle) -> Result<(), String> {
    let root_dir = std::env::current_dir().map_err(|err| format!("Failed to resolve current working directory: {err}"))?;

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
