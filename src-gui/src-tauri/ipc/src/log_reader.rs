use crate::connection::DEFAULT_APP_DIR;
use serde::{Deserialize, Serialize};
use std::fs;
use std::io::{self, BufRead};
use std::path::PathBuf;

#[derive(Debug, Serialize, Deserialize)]
pub struct LogResponse {
    pub logs: Vec<String>,
    pub total_lines: usize,
    pub log_file: String,
}

/// Get the daemon log file path
pub fn get_log_file_path() -> Result<PathBuf, String> {
    // Get the application data directory
    let home = dirs::home_dir().ok_or("Could not determine home directory")?;

    #[cfg(target_os = "macos")]
    let app_dir = home.join("Library").join("Caches").join(DEFAULT_APP_DIR);

    #[cfg(target_os = "windows")]
    let app_dir = home.join("AppData").join("Local").join(DEFAULT_APP_DIR);

    let log_file = app_dir.join("daemon.log");

    if !log_file.exists() {
        return Err(format!("Log file not found: {}", log_file.display()));
    }

    Ok(log_file)
}

/// Read last N lines from a file efficiently
pub fn read_last_n_lines(path: &PathBuf, n: usize) -> io::Result<Vec<String>> {
    let file = fs::File::open(path)?;
    let reader = io::BufReader::new(file);

    // Read all lines
    let lines: Vec<String> = reader.lines().filter_map(|line| line.ok()).collect();

    // Return last N lines
    if lines.len() <= n {
        Ok(lines)
    } else {
        Ok(lines[lines.len() - n..].to_vec())
    }
}

/// Read all lines from a file
pub fn read_all_lines(path: &PathBuf) -> io::Result<Vec<String>> {
    let file = fs::File::open(path)?;
    let reader = io::BufReader::new(file);

    Ok(reader.lines().filter_map(|line| line.ok()).collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use tempfile::NamedTempFile;

    #[test]
    fn test_read_last_n_lines() {
        // Create a temporary file with test data
        let mut temp_file = NamedTempFile::new().unwrap();
        for i in 1..=100 {
            writeln!(temp_file, "Line {}", i).unwrap();
        }

        let path = temp_file.path().to_path_buf();

        // Read last 10 lines
        let lines = read_last_n_lines(&path, 10).unwrap();
        assert_eq!(lines.len(), 10);
        assert_eq!(lines[0], "Line 91");
        assert_eq!(lines[9], "Line 100");
    }

    #[test]
    fn test_read_all_lines() {
        let mut temp_file = NamedTempFile::new().unwrap();
        for i in 1..=50 {
            writeln!(temp_file, "Line {}", i).unwrap();
        }

        let path = temp_file.path().to_path_buf();

        let lines = read_all_lines(&path).unwrap();
        assert_eq!(lines.len(), 50);
        assert_eq!(lines[0], "Line 1");
        assert_eq!(lines[49], "Line 50");
    }
}
