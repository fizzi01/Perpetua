use tokio_stream::StreamExt;
use futures::{sink::SinkExt};
use tokio::time::{Duration, timeout};
use tokio_util::codec::{FramedRead, FramedWrite, LinesCodecError, Decoder, Encoder};

use bytes::{Buf, BufMut, BytesMut};
use std::{cmp, io, str};

#[cfg(unix)]
use tokio::net::unix::{OwnedReadHalf, OwnedWriteHalf};

#[cfg(windows)]
use tokio::net::tcp::{OwnedReadHalf, OwnedWriteHalf};

pub mod connection;

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
        .map_err(|er| {
            io::Error::new(io::ErrorKind::InvalidData, "Unable to decode input as UTF8")})
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
                    // Validate the line by reading the last 4 bytes to extract the length prefix
                    let len_prefix = &line[line.len() - 4..];
                    
                    // Get the actual line content excluding the length prefix and newline
                    let line = &line[..line.len() - 4];
                    // Now check if the length prefix matches the actual line length
                    let expected_len = u32::from_be_bytes(len_prefix.try_into().unwrap()) as usize;
                    let actual_len = line.len(); // Exclude the length prefix
                    if expected_len != actual_len {
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

pub struct AsyncReader {
    #[cfg(unix)]
    reader: FramedRead<OwnedReadHalf, EventLinesCodec>,
    #[cfg(windows)]
    reader: FramedRead<OwnedReadHalf, EventLinesCodec>,
}

pub struct AsyncWriter {
    #[cfg(unix)]
    writer: FramedWrite<OwnedWriteHalf, EventLinesCodec>,
    #[cfg(windows)]
    writer: FramedWrite<OwnedWriteHalf, EventLinesCodec>,
}

impl AsyncReader {

    #[cfg(unix)]
    pub fn new(stream: OwnedReadHalf) -> Self
    {
        AsyncReader {
            reader: FramedRead::new(stream, EventLinesCodec::new()),
        }
    }
    
    #[cfg(windows)]
    pub fn new(stream: OwnedReadHalf) -> Self
    {
        AsyncReader {
            reader: FramedRead::new(stream, EventLinesCodec::new()),
        }
    }

    /// Reads a line from the underlying stream.
    pub async fn read_line(&mut self, t: &Duration) -> tokio::io::Result<Option<String>> {

        match timeout(*t, self.reader.next()).await {
            Err(_) => {
                // Timeout occurred
                Ok(None)
            },
            Ok(result) => match result {
                Some(Ok(line)) => Ok(Some(line)),
                Some(Err(e)) => {
                    Err(tokio::io::Error::new(tokio::io::ErrorKind::Other, format!("Failed to read line ({})", e)))},
                None => Ok(None),
            }
        }
    }
}

impl AsyncWriter {

    #[cfg(unix)]
    pub fn new(stream: OwnedWriteHalf) -> Self
    {
        AsyncWriter {
            writer: FramedWrite::new(stream, EventLinesCodec::new()),
        }
    }

    #[cfg(windows)]
    pub fn new(stream: WriteHalf<'a'>) -> Self
    {
        AsyncWriter {
            writer: FramedWrite::new(stream, EventLinesCodec::new()),
        }
    }

    /// Writes a line to the underlying stream.
    pub async fn write_line(&mut self, line: &str) -> tokio::io::Result<()> {
        self.writer.send(line.to_string()).await.map_err(|e| {
            tokio::io::Error::new(tokio::io::ErrorKind::Other, format!("Failed to write line ({})", e))
        })
    }
}


trait DataListener {
    async fn ping(&mut self) -> tokio::io::Result<()>;

    async fn listen<F>(&mut self, callback: F, t: &Duration) -> tokio::io::Result<()>
    where
        F: FnMut(String);

}

pub struct ConnectionHandler {
    reader: AsyncReader,
    writer: AsyncWriter,
    running: bool,
}

impl ConnectionHandler {
    pub fn new(reader: AsyncReader, writer: AsyncWriter) -> Self {
        ConnectionHandler { reader, writer , running: false }
    }

    pub fn get_writer(&mut self) -> &mut AsyncWriter {
        &mut self.writer
    }

    pub fn get_reader(&mut self) -> &mut AsyncReader {
        &mut self.reader
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

    async fn ping(&mut self) -> tokio::io::Result<()> {
        // Send a ping message to check connection
        self.writer.write_line("{\"command\": \"ping\"}").await.map_err(|e| {
            tokio::io::Error::new(tokio::io::ErrorKind::Other, format!("Failed to send ping ({})", e))
        })
    }

    async fn listen<F>(&mut self, mut callback: F, t: &Duration) -> tokio::io::Result<()>
    where
        F: FnMut(String),
    {
        self.running = true;
        while self.running {
            match self.reader.read_line(t).await {
                Ok(Some(line)) => {
                    callback(line);
                },
                Ok(None) => {
                    // Try to send a ping to check connection
                    // If it fails, we assume the connection is closed
                    if let Err(e) = self.ping().await {
                        self.running = false;
                        return Err(e);
                    }
                },
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
    use crate::connection::{DEFAULT_APP_DIR, DEFAULT_DAEMON_SOCKET};
    use crate::connection::{connect, default_path, DefaultPath, ConnectionError};
    use tokio::time::{Duration};
    use super::{ConnectionHandler, DataListener};

    #[tokio::test]
    #[cfg(unix)]
    async fn test_default_path_unix() {
        let path = default_path().unwrap();
        match path {
            DefaultPath::Unix(socket_path) => {
                let home = std::env::var("HOME").unwrap();
                let expected_path = std::path::Path::new(&home)
                    .join("Library")
                    .join("Caches")
                    .join(DEFAULT_APP_DIR)
                    .join(DEFAULT_DAEMON_SOCKET);
                assert_eq!(socket_path, expected_path);
            }
            _ => panic!("Expected Unix socket path"),
        }
    }

    #[tokio::test]
    #[cfg(windows)]
    async fn test_default_path_tcp() {
        let path = default_path().unwrap();
        match path {
            DefaultPath::Tcp(addr, port) => {
                assert_eq!(addr, "127.0.0.1");
                assert_eq!(port, DEFAULT_DAEMON_PORT);
            }
            _ => panic!("Expected TCP socket path"),
        }
    

    }    

    #[tokio::test]
    async fn test_connection_handler_and_connectio() {
        match connect().await {
            Ok((reader, writer)) => {
                let mut handler = ConnectionHandler::new(reader, writer);
                // Test sending a message
                let test_message = "{\"command\": \"ping\"}";
                handler.send_message(test_message).await.unwrap();

                // Test listening for messages
                if let Err(e) = handler.listen(|msg| {
                    println!("Received message: {}\n", msg);
                }, &Duration::from_secs(1)).await {
                    println!("Error while listening: {}\n", e);
                    assert!(false, "Listening failed");
                }

                println!("Connection established successfully.\n");

            }
            Err(e) => {
                // Since there's no server running, we expect a timeout or connection error.
                println!("Connection error: {}\n", e);
                assert!(matches!(e, ConnectionError::Timeout | ConnectionError::Io(_)));
            }
        }
        println!("Test completed.\n");
    }
    
}