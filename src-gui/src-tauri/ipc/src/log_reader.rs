/*
Perpetua - open-source and cross-platform KVM software.
Copyright (c) 2026 Federico Izzi

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <https://www.gnu.org/licenses/>.
*/

use crate::paths;
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

/// Get the daemon log file path.
///
/// Walks the candidate state directories from :mod:`paths` and returns the
/// first one that actually contains a log file.
pub fn get_log_file_path() -> Result<PathBuf, String> {
    let candidates = paths::log_file_paths();
    if candidates.is_empty() {
        return Err("Could not determine home directory".to_string());
    }
    for path in &candidates {
        if path.exists() {
            return Ok(path.clone());
        }
    }
    Err(format!(
        "Log file not found (looked in: {})",
        candidates
            .iter()
            .map(|p| p.display().to_string())
            .collect::<Vec<_>>()
            .join(", ")
    ))
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
