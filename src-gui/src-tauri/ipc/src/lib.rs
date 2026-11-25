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

use bytes::{BufMut, BytesMut};
use futures::sink::SinkExt;
use log;
use std::sync::Arc;
use std::{io, str};
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
pub mod paths;

pub use event::{CommandEvent, EventType, NotificationEvent};
pub use event::{EventParser, Parser};

/// Codec for the daemon IPC framing: `4-byte big-endian length + payload + b'\n'`.
///
/// The length comes first so a reader never has to search for the frame
/// boundary: it takes the count, then exactly that many bytes. An earlier
/// layout put the length *after* the payload, which forced a scan for the
/// trailing newline -- and a length whose big-endian bytes contain 0x0A
/// (2560..=2815, or any length == 10 mod 256) made that scan stop inside the
/// prefix and drop the frame.
///
/// The trailing newline is kept as an integrity marker: a frame that does not
/// end on one means the stream is out of sync, which is reported rather than
/// guessed at.
pub struct EventLinesCodec {
    /// Largest payload accepted, as a guard against a corrupt length.
    max_length: usize,
}

impl EventLinesCodec {
    pub fn new() -> EventLinesCodec {
        EventLinesCodec {
            max_length: MAX_FRAME_BYTES,
        }
    }
}

/// Generous ceiling on one payload: large enough for any status snapshot,
/// small enough that a corrupt length cannot drive an unbounded allocation.
pub const MAX_FRAME_BYTES: usize = 16 * 1024 * 1024;

/// Bytes a frame adds to its payload: the 4-byte length and the trailer.
const FRAME_OVERHEAD: usize = 5;

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
        if buf.len() < FRAME_OVERHEAD {
            return Ok(None);
        }

        let declared = u32::from_be_bytes(buf[..4].try_into().unwrap()) as usize;
        if declared > self.max_length {
            return Err(desync(format!(
                "frame of {declared} bytes exceeds the {} byte limit",
                self.max_length
            )));
        }

        // The trailer sits right after the payload; without it the frame is
        // simply not here yet.
        let trailer = 4 + declared;
        if buf.len() <= trailer {
            return Ok(None);
        }
        if buf[trailer] != b'\n' {
            // The length and the trailer disagree, so the stream is out of
            // sync and no later byte can be trusted to start a frame.
            return Err(desync(format!(
                "frame of {declared} bytes is not newline-terminated"
            )));
        }

        let frame = buf.split_to(trailer + 1);
        let line = without_carriage_return(&frame[4..trailer]);
        Ok(Some(utf8(line)?.to_string()))
    }
}

fn desync(detail: String) -> LinesCodecError {
    LinesCodecError::Io(io::Error::new(
        io::ErrorKind::InvalidData,
        format!("IPC stream out of sync: {detail}"),
    ))
}

impl<T> Encoder<T> for EventLinesCodec
where
    T: AsRef<str>,
{
    type Error = LinesCodecError;

    fn encode(&mut self, line: T, buf: &mut BytesMut) -> Result<(), LinesCodecError> {
        let line = line.as_ref();
        buf.reserve(line.len() + FRAME_OVERHEAD);
        // Length first, so the reader never has to search for the boundary.
        buf.put(&(line.len() as u32).to_be_bytes()[..]);
        buf.put(line.as_bytes());
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

    use super::{EventLinesCodec, MAX_FRAME_BYTES};
    use bytes::{BufMut, BytesMut};
    use tokio_util::codec::{Decoder, Encoder};

    /// Frame a payload: 4-byte big-endian length, payload, trailer.
    fn framed(payload: &str) -> BytesMut {
        let mut buf = BytesMut::new();
        buf.put(&(payload.len() as u32).to_be_bytes()[..]);
        buf.put(payload.as_bytes());
        buf.put_u8(b'\n');
        buf
    }

    fn payload_of_length(length: usize) -> String {
        let head = "{\"event_type\":\"command_success\",\"pad\":\"";
        let tail = "\"}";
        format!(
            "{}{}{}",
            head,
            "x".repeat(length - head.len() - tail.len()),
            tail
        )
    }

    #[test]
    fn round_trips_what_the_encoder_wrote() {
        let payload = payload_of_length(2737);
        let mut buf = BytesMut::new();
        let mut codec = EventLinesCodec::new();
        codec.encode(payload.as_str(), &mut buf).unwrap();

        assert_eq!(&buf[..4], &(payload.len() as u32).to_be_bytes()[..]);
        assert_eq!(
            codec.decode(&mut buf).unwrap().as_deref(),
            Some(payload.as_str())
        );
        assert!(buf.is_empty());
    }

    #[test]
    fn decodes_a_length_whose_bytes_contain_a_newline() {
        // 2737 == 0x00000AB1. Under the old layout the length sat after the
        // payload and that 0x0A was mistaken for the frame trailer.
        for length in [2737usize, 2560, 2815, 266] {
            let payload = payload_of_length(length);
            assert!((payload.len() as u32).to_be_bytes().contains(&b'\n'));
            let mut buf = framed(&payload);
            let mut codec = EventLinesCodec::new();
            assert_eq!(
                codec.decode(&mut buf).unwrap().as_deref(),
                Some(payload.as_str()),
                "length {length}"
            );
            assert!(buf.is_empty(), "leftover bytes for length {length}");
        }
    }

    #[test]
    fn decodes_back_to_back_frames() {
        let first = payload_of_length(2737);
        let second = "{\"event_type\":\"pong\"}".to_string();
        let mut buf = framed(&first);
        buf.unsplit(framed(&second));

        let mut codec = EventLinesCodec::new();
        assert_eq!(
            codec.decode(&mut buf).unwrap().as_deref(),
            Some(first.as_str())
        );
        assert_eq!(
            codec.decode(&mut buf).unwrap().as_deref(),
            Some(second.as_str())
        );
        assert_eq!(codec.decode(&mut buf).unwrap(), None);
    }

    #[test]
    fn a_partial_frame_is_kept_until_complete() {
        let payload = payload_of_length(2737);
        let full = framed(&payload);
        let mut buf = BytesMut::from(&full[..full.len() - 1]);

        let mut codec = EventLinesCodec::new();
        assert_eq!(codec.decode(&mut buf).unwrap(), None);

        buf.put_u8(b'\n');
        assert_eq!(
            codec.decode(&mut buf).unwrap().as_deref(),
            Some(payload.as_str())
        );
    }

    #[test]
    fn a_missing_trailer_is_reported_not_guessed_at() {
        let payload = "{\"event_type\":\"pong\"}";
        let mut buf = framed(payload);
        let trailer = buf.len() - 1;
        buf[trailer] = b'x';

        let mut codec = EventLinesCodec::new();
        assert!(codec.decode(&mut buf).is_err());
    }

    #[test]
    fn an_absurd_length_is_refused_rather_than_buffered() {
        let mut buf = BytesMut::new();
        buf.put(&((MAX_FRAME_BYTES + 1) as u32).to_be_bytes()[..]);
        buf.put(&b"payload\n"[..]);

        let mut codec = EventLinesCodec::new();
        assert!(codec.decode(&mut buf).is_err());
    }

    #[tokio::test]
    async fn test_connection_handler_and_connectio() {
        match connect(
            Duration::from_secs(1),
            Duration::from_secs(1),
            Duration::from_secs(2),
        )
        .await
        {
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
