/*
 Perpatua - open-source and cross-platform KVM software.
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

use bytes::{Buf, BufMut, BytesMut};
use futures::sink::SinkExt;
use log;
use std::sync::Arc;
use std::{cmp, io, str};
use tokio::sync::{Mutex, MutexGuard};
use tokio::time::{Duration, timeout};
use tokio_stream::StreamExt;
use tokio_util::codec::{Decoder, Encoder, FramedRead, FramedWrite, LinesCodecError};

#[cfg(unix)]
use tokio::net::unix::{OwnedReadHalf, OwnedWriteHalf};

#[cfg(windows)]
use tokio::net::tcp::{OwnedReadHalf, OwnedWriteHalf};

pub mod connection;
pub mod event;
pub mod log_reader;

pub use event::{CommandEvent, EventType, NotificationEvent};
pub use event::{EventParser, Parser};

pub struct EventLinesCodec {
    // Stored index of the next index to examine for a `\n` character.
    // This is used to optimize searching.
    // For example, if `decode` was called with `abc`, it would hold `3`,
    // because that is the next index to examine.
    // The next time `decode` is called with `abcde\n`, the method will
    // only look at `de\n` before returning.
    next_index: usize,

    /// The maximum length for a given line. If `usize::MAX`, lines will be
    /// read until a `\n` character is reached.
    max_length: usize,

    /// Are we currently discarding the remainder of a line which was over
    /// the length limit?
    is_discarding: bool,
}

impl EventLinesCodec {
    pub fn new() -> EventLinesCodec {
        EventLinesCodec {
            next_index: 0,
            max_length: usize::MAX,
            is_discarding: false,
        }
    }
}

fn utf8(buf: &[u8]) -> Result<&str, io::Error> {
    str::from_utf8(buf)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "Unable to decode input as UTF8"))
}

fn without_carriage_return(s: &[u8]) -> &[u8] {
    if let Some(&b'\r') = s.last() {
        &s[..s.len() - 1]
    } else {
        s
    }
}

impl Decoder for EventLinesCodec {
    type Item = String;
    type Error = LinesCodecError;

    fn decode(&mut self, buf: &mut BytesMut) -> Result<Option<String>, LinesCodecError> {
        loop {
            // Determine how far into the buffer we'll search for a newline. If
            // there's no max_length set, we'll read to the end of the buffer.
            let read_to = cmp::min(self.max_length.saturating_add(1), buf.len());

            let newline_offset = buf[self.next_index..read_to]
                .iter()
                .position(|b| *b == b'\n');

            match (self.is_discarding, newline_offset) {
                (true, Some(offset)) => {
                    // If we found a newline, discard up to that offset and
                    // then stop discarding. On the next iteration, we'll try
                    // to read a line normally.
                    buf.advance(offset + self.next_index + 1);
                    self.is_discarding = false;
                    self.next_index = 0;
                }
                (true, None) => {
                    // Otherwise, we didn't find a newline, so we'll discard
                    // everything we read. On the next iteration, we'll continue
                    // discarding up to max_len bytes unless we find a newline.
                    buf.advance(read_to);
                    self.next_index = 0;
                    if buf.is_empty() {
                        return Ok(None);
                    }
                }
                (false, Some(offset)) => {
                    // Found a line!
                    let newline_index = offset + self.next_index;
                    self.next_index = 0;
                    let line = buf.split_to(newline_index + 1);
                    // Remove the newline character
                    let line = &line[..line.len() - 1];
                    // Check if the line has at least 4 bytes for length prefix
                    if line.len() < 4 {
                        return Ok(None);
                    }
                    // Validate the line by reading the last 4 bytes to extract the length prefix
                    let len_prefix = &line[line.len() - 4..];

                    // Get the actual line content excluding the length prefix and newline
                    let line = &line[..line.len() - 4];
                    // Now check if the length prefix matches the actual line length
                    let expected_len = u32::from_be_bytes(len_prefix.try_into().unwrap()) as usize;
                    let actual_len = line.len(); // Exclude the length prefix
                    if expected_len != actual_len {
                        // Before advance we need to check if buf has enough data
                        if buf.len() < read_to {
                            return Ok(None);
                        }
                        
                        // Discard the line if lengths do not match
                        buf.advance(read_to);
                        self.next_index = 0;
                        if buf.is_empty() {
                            return Ok(None);
                        }
                    }

                    let line = without_carriage_return(line);
                    let line = utf8(line)?;

                    return Ok(Some(line.to_string()));
                }
                (false, None) if buf.len() > self.max_length => {
                    // Reached the maximum length without finding a
                    // newline, return an error and start discarding on the
                    // next call.
                    self.is_discarding = true;
                    return Err(LinesCodecError::MaxLineLengthExceeded);
                }
                (false, None) => {
                    // We didn't find a line or reach the length limit, so the next
                    // call will resume searching at the current offset.
                    self.next_index = read_to;
                    return Ok(None);
                }
            }
        }
    }
}

impl<T> Encoder<T> for EventLinesCodec
where
    T: AsRef<str>,
{
    type Error = LinesCodecError;

    fn encode(&mut self, line: T, buf: &mut BytesMut) -> Result<(), LinesCodecError> {
        let line = line.as_ref();
        buf.reserve(line.len() + 1);
        buf.put(line.as_bytes());
        // Put line length prefix encoded as big-endian
        let len_prefix = (line.len() as u32).to_be_bytes();
        buf.put(&len_prefix[..]);
        buf.put_u8(b'\n');
        Ok(())
    }
}

#[derive(Clone)]
pub struct AtomicAsyncWriter {
    inner: Arc<Mutex<FramedWrite<OwnedWriteHalf, EventLinesCodec>>>,
}

impl AtomicAsyncWriter {
    pub fn new(stream: OwnedWriteHalf) -> Self {
        AtomicAsyncWriter {
            inner: Arc::new(Mutex::new(FramedWrite::new(stream, EventLinesCodec::new()))),
        }
    }

    pub async fn lock(&self) -> MutexGuard<'_, FramedWrite<OwnedWriteHalf, EventLinesCodec>> {
        self.inner.lock().await
    }

    pub fn clone(&self) -> Self {
        AtomicAsyncWriter {
            inner: Arc::clone(&self.inner),
        }
    }

    pub async fn send(&self, line: String) -> tokio::io::Result<()> {
        let mut writer = self.lock().await;
        writer.send(line).await.map_err(|e| {
            tokio::io::Error::new(
                tokio::io::ErrorKind::Other,
                format!("Failed to send line ({})", e),
            )
        })
    }
}

#[derive(Clone)]
pub struct AtomicAsyncReader {
    inner: Arc<Mutex<FramedRead<OwnedReadHalf, EventLinesCodec>>>,
}

impl AtomicAsyncReader {
    pub async fn lock(&self) -> MutexGuard<'_, FramedRead<OwnedReadHalf, EventLinesCodec>> {
        self.inner.lock().await
    }

    pub fn clone(&self) -> Self {
        AtomicAsyncReader {
            inner: Arc::clone(&self.inner),
        }
    }

    pub async fn read_line(&self, t: &Duration) -> tokio::io::Result<Option<String>> {
        let mut reader = self.lock().await;

        match timeout(*t, reader.next()).await {
            Err(_) => {
                // Timeout occurred
                Ok(None)
            }
            Ok(result) => match result {
                Some(Ok(line)) => Ok(Some(line)),
                Some(Err(e)) => Err(tokio::io::Error::new(
                    tokio::io::ErrorKind::Other,
                    format!("Failed to read line ({})", e),
                )),
                None => Ok(None),
            },
        }
    }
}

#[derive(Clone)]
pub struct AsyncWriter {
    writer: AtomicAsyncWriter,
}

impl AsyncWriter {
    pub fn new(stream: OwnedWriteHalf) -> Self {
        AsyncWriter {
            writer: AtomicAsyncWriter::new(stream),
        }
    }

    pub fn get_writer(&self) -> &AtomicAsyncWriter {
        &self.writer
    }

    /// Writes a line to the underlying stream.
    pub async fn write_line(&self, line: &str) -> tokio::io::Result<()> {
        let w = self.get_writer();
        w.send(line.to_string()).await
    }
}

#[derive(Clone)]
pub struct AsyncReader {
    reader: AtomicAsyncReader,
}

impl AsyncReader {
    pub fn new(stream: OwnedReadHalf) -> Self {
        AsyncReader {
            reader: AtomicAsyncReader {
                inner: Arc::new(Mutex::new(FramedRead::new(stream, EventLinesCodec::new()))),
            },
        }
    }

    pub fn get_reader(&self) -> &AtomicAsyncReader {
        &self.reader
    }

    /// Reads a line from the underlying stream.
    pub async fn read_line(&mut self, t: &Duration) -> tokio::io::Result<Option<String>> {
        let reader = self.get_reader();
        reader.read_line(t).await
    }
}

pub trait DataListener {
    /// Send a ping message to check connection
    fn ping(&self) -> impl Future<Output = tokio::io::Result<()>>;

    /// Listen for incoming messages and invoke the callback for each message received
    /// ### Arguments
    /// * `callback` - A closure that will be called with each received message
    /// * `t` - Duration to wait for messages before timing out
    fn listen<F>(
        &mut self,
        callback: F,
        t: &Duration,
    ) -> impl Future<Output = tokio::io::Result<()>>
    where
        F: FnMut(NotificationEvent);
}

pub struct ConnectionHandler {
    reader: AsyncReader,
    writer: AsyncWriter,
    running: bool,
}

impl ConnectionHandler {
    pub fn new(reader: AsyncReader, writer: AsyncWriter) -> Self {
        ConnectionHandler {
            reader,
            writer,
            running: false,
        }
    }

    pub fn get_writer(&self) -> &AsyncWriter {
        &self.writer
    }

    pub fn get_reader(&self) -> &AsyncReader {
        &self.reader
    }

    pub fn is_running(&self) -> bool {
        self.running
    }

    pub fn stop(&mut self) {
        self.running = false;
    }

    pub async fn send_message(&mut self, message: &str) -> tokio::io::Result<()> {
        self.writer.write_line(message).await
    }
}

impl DataListener for ConnectionHandler {
    async fn ping(&self) -> tokio::io::Result<()> {
        self.writer
            .write_line("{\"command\": \"ping\"}")
            .await
            .map_err(|e| {
                tokio::io::Error::new(
                    tokio::io::ErrorKind::Other,
                    format!("Failed to send ping ({})", e),
                )
            })
    }

    async fn listen<F>(&mut self, mut callback: F, t: &Duration) -> tokio::io::Result<()>
    where
        F: FnMut(NotificationEvent),
    {
        self.running = true;
        while self.running {
            match self.reader.read_line(t).await {
                Ok(Some(line)) => {
                    let parsed_event: NotificationEvent = match EventParser::parse_json(&line) {
                        Ok(event) => event,
                        Err(e) => {
                            log::error!("Error in parsing event ({})\nEvent => {}", e, line);
                            continue;
                        }
                    };
                    callback(parsed_event);
                }
                Ok(None) => {
                    // Try to send a ping to check connection
                    // If it fails, we assume the connection is closed
                    if let Err(e) = self.ping().await {
                        self.running = false;
                        return Err(e);
                    }
                }
                Err(e) => {
                    return Err(e);
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{ConnectionHandler, DataListener};
    use crate::connection::{ConnectionError, connect};
    use tokio::time::Duration;

    #[tokio::test]
    async fn test_connection_handler_and_connectio() {
        match connect(Duration::from_secs(1)).await {
            Ok((reader, writer)) => {
                let mut handler = ConnectionHandler::new(reader, writer);
                // Test sending a message
                let test_message = "{\"command\": \"ping\"}";
                handler.send_message(test_message).await.unwrap();

                // Test listening for messages
                if let Err(e) = handler
                    .listen(
                        |msg| {
                            dbg!(msg);
                        },
                        &Duration::from_secs(1),
                    )
                    .await
                {
                    println!("Error while listening: {}\n", e);
                    assert!(false, "Listening failed");
                }

                println!("Connection established successfully.\n");
            }
            Err(e) => {
                // Since there's no server running, we expect a timeout or connection error.
                println!("Connection error: {}\n", e);
                assert!(matches!(
                    e,
                    ConnectionError::Timeout | ConnectionError::Io(_)
                ));
            }
        }
        println!("Test completed.\n");
    }
}
