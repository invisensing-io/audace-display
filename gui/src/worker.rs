//! The Python worker process: spawning it, writing requests, and reading its
//! messages on background threads so the UI never waits on it.

use std::collections::VecDeque;
use std::io::{BufReader, Write};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Mutex};
use std::thread;

use serde_json::{Map, Value, json};

use crate::i18n::{Msg, Text};
use crate::protocol::{self, Message};

/// The module the worker runs as (`python -m ...`).
pub const WORKER_MODULE: &str = "audace_display.gui_worker";

/// Lines of the worker's stderr kept for the log window.
const STDERR_LINES: usize = 400;

/// The default interpreter command of this platform.
pub fn default_interpreter() -> &'static str {
    if cfg!(windows) { "py -3" } else { "python3" }
}

/// The interpreter tried when the default one is not installed (Windows
/// without the `py` launcher).
pub fn fallback_interpreter(command: &str) -> Option<&'static str> {
    (cfg!(windows) && command.trim() == "py -3").then_some("python")
}

/// Splits an interpreter command into program and arguments. Words are
/// separated by blanks; double or single quotes keep a path with spaces in
/// one word (`"C:\Program Files\Python312\python.exe" -X utf8`). A backslash
/// is an ordinary character, as in a Windows path.
pub fn split_command(command: &str) -> Result<Vec<String>, Text> {
    let mut words = Vec::new();
    let mut word = String::new();
    let mut in_word = false;
    let mut quote: Option<char> = None;
    for c in command.chars() {
        match quote {
            Some(q) if c == q => quote = None,
            Some(_) => word.push(c),
            None if c == '"' || c == '\'' => {
                quote = Some(c);
                in_word = true;
            }
            None if c.is_whitespace() => {
                if in_word {
                    words.push(std::mem::take(&mut word));
                    in_word = false;
                }
            }
            None => {
                word.push(c);
                in_word = true;
            }
        }
    }
    if let Some(q) = quote {
        return Err(Text::from(Msg::UnbalancedQuote).arg("quote", q.to_string()));
    }
    if in_word {
        words.push(word);
    }
    if words.is_empty() {
        return Err(Msg::EmptyCommand.into());
    }
    Ok(words)
}

/// What the reader threads report.
#[derive(Debug)]
pub enum WorkerEvent {
    Message(Message),
    /// The worker's output ended or became unreadable; the text says why.
    Stopped(Text),
}

/// Called after each event, to wake the UI loop up.
pub type Waker = Arc<dyn Fn() + Send + Sync>;

/// A running worker.
pub struct WorkerProcess {
    child: Child,
    stdin: Option<ChildStdin>,
    events: Mutex<Receiver<WorkerEvent>>,
    stderr: Arc<Mutex<VecDeque<String>>>,
    next_id: u64,
    /// The command line it was started with, for messages.
    pub command_line: String,
}

impl WorkerProcess {
    /// Starts `<interpreter> -m audace_display.gui_worker`.
    pub fn spawn(interpreter: &str, waker: Waker) -> Result<WorkerProcess, Text> {
        let words = split_command(interpreter)?;
        let mut cmd = Command::new(&words[0]);
        cmd.args(&words[1..])
            .args(["-m", WORKER_MODULE])
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .env("MPLBACKEND", "Agg")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            const CREATE_NO_WINDOW: u32 = 0x0800_0000;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        let command_line = format!("{} -m {WORKER_MODULE}", words.join(" "));
        let mut child = cmd.spawn().map_err(|e| {
            Text::from(Msg::CannotStart)
                .arg("command", command_line.as_str())
                .arg("error", e.to_string())
        })?;
        let stdout = child.stdout.take().expect("piped stdout");
        let stderr_pipe = child.stderr.take().expect("piped stderr");
        let stdin = child.stdin.take();

        let (tx, rx) = mpsc::channel();
        let stderr = Arc::new(Mutex::new(VecDeque::new()));
        spawn_stdout_reader(stdout, tx, waker.clone(), stderr.clone());
        spawn_stderr_reader(stderr_pipe, stderr.clone(), waker);
        Ok(WorkerProcess {
            child,
            stdin,
            events: Mutex::new(rx),
            stderr,
            next_id: 0,
            command_line,
        })
    }

    /// Sends `{"id": <new>, "cmd": cmd, ...fields}`; returns the id.
    pub fn request(&mut self, cmd: &str, fields: Map<String, Value>) -> Result<u64, Text> {
        self.next_id += 1;
        let id = self.next_id;
        let mut req = fields;
        req.insert("id".into(), json!(id));
        req.insert("cmd".into(), json!(cmd));
        self.write(&Value::Object(req))?;
        Ok(id)
    }

    /// Asks the worker to stop request `id`.
    pub fn cancel(&mut self, id: u64) -> Result<(), Text> {
        self.write(&json!({"cmd": "cancel", "target": id}))
    }

    fn write(&mut self, request: &Value) -> Result<(), Text> {
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| Text::from(Msg::InputClosed))?;
        stdin
            .write_all(&protocol::encode_request(request))
            .and_then(|_| stdin.flush())
            .map_err(|e| Text::from(Msg::WriteFailed).arg("error", e.to_string()))
    }

    /// The events received since the last call.
    pub fn drain(&self) -> Vec<WorkerEvent> {
        let rx = self.events.lock().expect("worker event lock");
        rx.try_iter().collect()
    }

    /// The last lines the worker wrote to stderr.
    pub fn stderr_tail(&self, lines: usize) -> Vec<String> {
        let log = self.stderr.lock().expect("worker stderr lock");
        let skip = log.len().saturating_sub(lines);
        log.iter().skip(skip).cloned().collect()
    }

    /// Stops the process now (a cancel that did not come back in time).
    pub fn kill(&mut self) {
        self.stdin = None;
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Drop for WorkerProcess {
    fn drop(&mut self) {
        // Closing its input ends the worker's loop; kill in case it is busy.
        let _ = self.write(&json!({"id": 0, "cmd": "shutdown"}));
        self.kill();
    }
}

fn spawn_stdout_reader(
    stdout: std::process::ChildStdout,
    tx: Sender<WorkerEvent>,
    waker: Waker,
    stderr: Arc<Mutex<VecDeque<String>>>,
) {
    thread::Builder::new()
        .name("worker-stdout".into())
        .spawn(move || {
            let mut reader = BufReader::with_capacity(1 << 20, stdout);
            loop {
                let event = match protocol::read_message(&mut reader) {
                    Ok(Some(m)) => WorkerEvent::Message(m),
                    Ok(None) => {
                        WorkerEvent::Stopped(stopped_text(Msg::WorkerExited.into(), &stderr))
                    }
                    Err(e) => WorkerEvent::Stopped(stopped_text(e.text(), &stderr)),
                };
                let stop = matches!(event, WorkerEvent::Stopped(_));
                if tx.send(event).is_err() {
                    return;
                }
                waker();
                if stop {
                    return;
                }
            }
        })
        .expect("spawning the worker reader thread");
}

/// `why`, with the worker's last stderr lines (a Python traceback, "No
/// module named ..."), which usually say the real reason.
fn stopped_text(why: Text, stderr: &Arc<Mutex<VecDeque<String>>>) -> Text {
    // The stderr thread may still be draining the last lines.
    thread::sleep(std::time::Duration::from_millis(150));
    let log = stderr.lock().expect("worker stderr lock");
    let tail: Vec<&str> = log.iter().rev().take(12).map(String::as_str).collect();
    if tail.is_empty() {
        why
    } else {
        let lines: Vec<&str> = tail.into_iter().rev().collect();
        why.with_detail(lines.join("\n"))
    }
}

fn spawn_stderr_reader(
    stderr: std::process::ChildStderr,
    log: Arc<Mutex<VecDeque<String>>>,
    waker: Waker,
) {
    thread::Builder::new()
        .name("worker-stderr".into())
        .spawn(move || {
            use std::io::BufRead;
            let reader = BufReader::new(stderr);
            for line in reader.split(b'\n') {
                let Ok(line) = line else { return };
                let text = String::from_utf8_lossy(&line).trim_end().to_string();
                let mut log = log.lock().expect("worker stderr lock");
                if log.len() == STDERR_LINES {
                    log.pop_front();
                }
                log.push_back(text);
                drop(log);
                waker();
            }
        })
        .expect("spawning the worker stderr thread");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn splits_plain_words() {
        assert_eq!(split_command("py -3").unwrap(), vec!["py", "-3"]);
        assert_eq!(split_command("  python3  ").unwrap(), vec!["python3"]);
    }

    #[test]
    fn keeps_quoted_paths_with_spaces_and_backslashes() {
        assert_eq!(
            split_command(r#""C:\Program Files\Python312\python.exe" -X utf8"#).unwrap(),
            vec![r"C:\Program Files\Python312\python.exe", "-X", "utf8"]
        );
        assert_eq!(
            split_command("'/opt/my env/bin/python'").unwrap(),
            vec!["/opt/my env/bin/python"]
        );
        assert_eq!(
            split_command(r"C:\Python312\python.exe").unwrap(),
            vec![r"C:\Python312\python.exe"]
        );
    }

    #[test]
    fn refuses_empty_and_unbalanced_commands() {
        assert!(split_command("   ").is_err());
        assert!(split_command("\"C:\\Python").is_err());
    }

    #[test]
    fn the_default_interpreter_follows_the_platform() {
        if cfg!(windows) {
            assert_eq!(default_interpreter(), "py -3");
            assert_eq!(fallback_interpreter("py -3"), Some("python"));
        } else {
            assert_eq!(default_interpreter(), "python3");
            assert_eq!(fallback_interpreter("python3"), None);
        }
    }

    #[test]
    fn a_missing_interpreter_is_an_error_not_a_panic() {
        let waker: Waker = Arc::new(|| {});
        let err = WorkerProcess::spawn("/no/such/python-interpreter", waker)
            .err()
            .unwrap()
            .render(crate::i18n::Lang::En);
        assert!(err.contains("cannot start"), "{err}");
    }

    /// End to end with a real worker: set `AUDACE_GUI_TEST_PYTHON` to an
    /// interpreter that has audace-display installed.
    #[test]
    fn talks_to_a_real_worker_when_one_is_configured() {
        let Ok(python) = std::env::var("AUDACE_GUI_TEST_PYTHON") else {
            eprintln!("AUDACE_GUI_TEST_PYTHON is not set: skipped");
            return;
        };
        let waker: Waker = Arc::new(|| {});
        let mut w = WorkerProcess::spawn(&python, waker).expect("spawn");
        let mut fields = Map::new();
        fields.insert("protocol".into(), json!(protocol::PROTOCOL));
        let id = w.request("hello", fields).unwrap();
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(60);
        loop {
            for ev in w.drain() {
                match ev {
                    WorkerEvent::Message(m) if m.id() == Some(id) => {
                        assert_eq!(m.kind(), "hello", "{:?}", m.header);
                        let hello: protocol::Hello = m.parse().unwrap();
                        assert_eq!(hello.protocol, protocol::PROTOCOL);
                        assert!(hello.views.contains_key("heatmap"));
                        return;
                    }
                    WorkerEvent::Stopped(why) => {
                        panic!("the worker stopped: {}", why.render(crate::i18n::Lang::En))
                    }
                    _ => {}
                }
            }
            assert!(
                std::time::Instant::now() < deadline,
                "no hello from the worker"
            );
            thread::sleep(std::time::Duration::from_millis(20));
        }
    }
}
